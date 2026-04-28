import base64
import io
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


APP_DIR = Path(__file__).parent
DEFAULT_TEMPLATE = APP_DIR / "template_invoice.pdf"


# -------------------------
# Formatting helpers
# -------------------------

def parse_amount(value):
    """Accepts 13285, '€ 13,285', '13.285,00', '13285.00' and returns float."""
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()

    if not s:
        return 0.0

    s = s.replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "")

    # Greek format: 13.285,00
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")

    elif "," in s:
        # If comma is decimal separator with 2 digits, use decimal,
        # otherwise treat comma as thousand separator.
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        return float(s)
    except Exception:
        cleaned = re.sub(r"[^0-9.-]", "", s)
        return float(cleaned) if cleaned else 0.0


def fmt_eur(value):
    """Format number as Greek money style: 13.285,00"""
    try:
        n = float(value)
    except Exception:
        n = 0.0

    s = f"{n:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_rate(value):
    try:
        n = float(str(value).replace(",", "."))
    except Exception:
        n = 0.0

    if abs(n - int(n)) < 0.000001:
        return str(int(n))

    return str(n).replace(".", ",")


def parse_date_any(value):
    if not value:
        return ""

    s = str(value).strip()

    # Already dd/mm/yyyy
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)
    if m:
        d, mo, y = m.groups()
        y = "20" + y if len(y) == 2 else y
        return f"{int(d):02d}/{int(mo):02d}/{int(y):04d}"

    # Booking style: Mon, Jul 6, 2026
    date_formats = [
        "%a, %b %d, %Y",
        "%A, %b %d, %Y",
        "%b %d, %Y",
        "%d %b %Y",
        "%Y-%m-%d",
    ]

    for fmt in date_formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except Exception:
            pass

    return s


# -------------------------
# Greek amount in words
# -------------------------

def under_1000(n, female=False):
    units_m = [
        "",
        "ένα",
        "δύο",
        "τρία",
        "τέσσερα",
        "πέντε",
        "έξι",
        "επτά",
        "οκτώ",
        "εννέα",
    ]

    units_f = [
        "",
        "μία",
        "δύο",
        "τρεις",
        "τέσσερις",
        "πέντε",
        "έξι",
        "επτά",
        "οκτώ",
        "εννέα",
    ]

    teens = [
        "δέκα",
        "έντεκα",
        "δώδεκα",
        "δεκατρία",
        "δεκατέσσερα",
        "δεκαπέντε",
        "δεκαέξι",
        "δεκαεπτά",
        "δεκαοκτώ",
        "δεκαεννέα",
    ]

    tens = [
        "",
        "",
        "είκοσι",
        "τριάντα",
        "σαράντα",
        "πενήντα",
        "εξήντα",
        "εβδομήντα",
        "ογδόντα",
        "ενενήντα",
    ]

    hundreds = [
        "",
        "εκατό",
        "διακόσια",
        "τριακόσια",
        "τετρακόσια",
        "πεντακόσια",
        "εξακόσια",
        "επτακόσια",
        "οκτακόσια",
        "εννιακόσια",
    ]

    units = units_f if female else units_m
    parts = []

    h, r = divmod(int(n), 100)

    if h:
        parts.append(hundreds[h])

    if 10 <= r < 20:
        parts.append(teens[r - 10])
    else:
        t, u = divmod(r, 10)

        if t:
            parts.append(tens[t])

        if u:
            parts.append(units[u])

    return " ".join(parts)


def int_words(n):
    n = int(abs(n))

    if n == 0:
        return "μηδέν"

    parts = []

    millions, n = divmod(n, 1_000_000)
    thousands, rest = divmod(n, 1000)

    if millions:
        if millions == 1:
            parts.append("ένα εκατομμύριο")
        else:
            parts.append(under_1000(millions) + " εκατομμύρια")

    if thousands:
        if thousands == 1:
            parts.append("χίλια")
        else:
            parts.append(under_1000(thousands, female=True) + " χιλιάδες")

    if rest:
        parts.append(under_1000(rest))

    return " ".join(parts)


def amount_words_gr(amount):
    amount = float(amount or 0)

    cents = int(round(abs(amount) * 100)) % 100
    euros = int(math.floor(abs(amount)))

    prefix = "μείον " if amount < 0 else ""

    text = f"{prefix}{int_words(euros)} ευρώ και {int_words(cents)} λεπτά"

    return text[:1].upper() + text[1:]


# -------------------------
# OpenAI vision extraction
# -------------------------

def get_openai_api_key():
    key = None

    try:
        key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        pass

    return key or os.getenv("OPENAI_API_KEY")


def extract_booking_from_image(image_file):
    api_key = get_openai_api_key()

    if not api_key or OpenAI is None:
        return None, (
            "Δεν βρέθηκε OPENAI_API_KEY. "
            "Συμπλήρωσε τα πεδία χειροκίνητα ή βάλε το key στα Streamlit Secrets."
        )

    image_bytes = image_file.getvalue()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = image_file.type or "image/png"
    data_url = f"data:{mime};base64,{b64}"

    client = OpenAI(api_key=api_key)

    prompt = """
Read this Booking.com reservation screenshot and return ONLY valid JSON.
Extract these fields exactly if visible:
{
  "guest_name": "",
  "booking_number": "",
  "check_in": "",
  "check_out": "",
  "nights": "",
  "total_price": "",
  "channel": "Booking.com",
  "commissionable_amount": "",
  "commission_and_charges": ""
}

Rules:
- total_price must be the total price shown for the reservation, not commission.
- Keep dates as visible if unsure.
- Use empty string for missing fields.
- Do not include markdown.
"""

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
        )

        text = response.output_text.strip()
        text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.S)

        return json.loads(text), None

    except Exception as e:
        return None, f"Δεν μπόρεσα να διαβάσω αυτόματα την εικόνα: {e}"


# -------------------------
# PDF fill
# -------------------------

def fill_pdf(template_path, field_values):
    reader = PdfReader(str(template_path))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update(
            {
                NameObject("/AcroForm"): reader.trailer["/Root"]["/AcroForm"]
            }
        )

        writer.set_need_appearances_writer(True)

        writer._root_object[NameObject("/AcroForm")][
            NameObject("/NeedAppearances")
        ] = BooleanObject(True)

    for page in writer.pages:
        writer.update_page_form_field_values(
            page,
            field_values,
            auto_regenerate=True,
        )

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    return out


def build_invoice_fields(data, business, options):
    check_in = parse_date_any(data.get("check_in"))
    check_out = parse_date_any(data.get("check_out"))

    nights = str(data.get("nights") or "").strip()
    nights_num = int(re.sub(r"[^0-9]", "", nights) or "0")

    total = parse_amount(data.get("total_price"))

    # Κρατάμε ακριβώς το total price στην Αξία.
    # Η τιμή μονάδας είναι μόνο ενημερωτική.
    unit = total / nights_num if nights_num else 0.0

    if options.get("include_stamp"):
        stamp_rate = float(str(options.get("stamp_rate", 0)).replace(",", ".") or 0)
    else:
        stamp_rate = 0.0

    vat_rate = float(str(options.get("vat_rate", 0)).replace(",", ".") or 0)

    net = total
    stamp = round(net * stamp_rate / 100, 2)
    vat = round(net * vat_rate / 100, 2)
    payable = round(net + stamp + vat, 2)

    desc = options.get("description") or (
        f"{nights_num}-night accommodation from {check_in} to {check_out}"
    )

    booking_ref = options.get("booking_ref") or (
        f"Booking.com reservation no. {data.get('booking_number', '')}"
    )

    return {
        "business_name": business.get("name", ""),
        "business_address": business.get("address", ""),
        "business_phone": business.get("phone", ""),
        "business_email": business.get("email", ""),
        "business_afm": business.get("afm", ""),
        "business_doy": business.get("doy", ""),

        "series": str(options.get("series", "1")),
        "document_number": str(options.get("document_number", "1")),
        "document_date": options.get(
            "document_date",
            datetime.today().strftime("%d/%m/%Y"),
        ),

        "customer_name": data.get("guest_name", ""),
        "customer_afm": "",
        "customer_doy": "",
        "customer_address": "",
        "customer_phone": "",

        "item1_desc": desc,
        "item1_qty": str(nights_num) if nights_num else "",
        "item1_unit": fmt_eur(unit) if unit else "",
        "item1_amount": fmt_eur(net),

        "item2_desc": "",
        "item2_qty": "",
        "item2_unit": "",
        "item2_amount": "",

        "item3_desc": "",
        "item3_qty": "",
        "item3_unit": "",
        "item3_amount": "",

        "item4_desc": "",
        "item4_qty": "",
        "item4_unit": "",
        "item4_amount": "",

        "item5_desc": "",
        "item5_qty": "",
        "item5_unit": "",
        "item5_amount": "",

        "notes": options.get("notes", ""),
        "booking_ref": booking_ref,

        "amount_in_words": amount_words_gr(payable),

        "net_amount": fmt_eur(net),
        "stamp_rate": fmt_rate(stamp_rate),
        "stamp_duty": fmt_eur(stamp),
        "vat_rate": fmt_rate(vat_rate),
        "vat_amount": fmt_eur(vat),
        "total_amount": fmt_eur(payable),
        "payable_amount": fmt_eur(payable),

        "payment_method": "Πλατφόρμα Booking.com",
        "issuer_signature": "",
        "customer_signature": "",
        "business_stamp": "",
    }


# -------------------------
# Streamlit UI
# -------------------------

st.set_page_config(
    page_title="Booking.com -> Απόδειξη PDF",
    layout="wide",
)


# -------------------------
# Private password login
# -------------------------

APP_PASSWORD = st.secrets.get("APP_PASSWORD", os.getenv("APP_PASSWORD", ""))

if APP_PASSWORD:
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if not st.session_state.logged_in:
        st.title("🔐 Private Access")
        st.write("Βάλε τον κωδικό πρόσβασης για να ανοίξει η εφαρμογή.")

        password = st.text_input("Κωδικός πρόσβασης", type="password")

        col_login, col_info = st.columns([1, 3])

        with col_login:
            login_clicked = st.button("Είσοδος", type="primary")

        if login_clicked:
            if password == APP_PASSWORD:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Λάθος κωδικός πρόσβασης.")

        st.stop()


st.title("Booking.com -> Αυτόματη Απόδειξη / Τιμολόγιο PDF")
st.write(
    "Ανεβάζεις screenshot κράτησης Booking.com, "
    "ελέγχεις τα στοιχεία και κατεβάζεις συμπληρωμένο PDF."
)


with st.sidebar:
    st.header("Στοιχεία επιχείρησης")

    business = {
        "name": st.text_input(
            "Επωνυμία",
            "Spacious Beachfront Prop (maisonette)",
        ),
        "address": st.text_input(
            "Διεύθυνση",
            "270 Navarinou Street, Paralia Vergas, Kalamata, Greece",
        ),
        "phone": st.text_input(
            "Τηλ.",
            "+306972245943",
        ),
        "email": st.text_input(
            "Email",
            "dimmaik@gmail.com",
        ),
        "afm": st.text_input(
            "Α.Φ.Μ.",
            "106028465",
        ),
        "doy": st.text_input(
            "Δ.Ο.Υ.",
            "ΚΑΛΑΜΑΤΑΣ",
        ),
    }

    st.divider()

    st.header("Ρυθμίσεις")

    include_stamp = st.checkbox(
        "Υπολόγισε χαρτόσημο",
        value=False,
    )

    stamp_rate = st.text_input(
        "Χαρτόσημο %",
        "3,6",
    )

    vat_rate = st.text_input(
        "Φ.Π.Α. %",
        "0",
    )

    st.divider()

    if st.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()


uploaded = st.file_uploader(
    "Ανέβασε screenshot από Booking.com",
    type=["png", "jpg", "jpeg", "webp"],
)


if "data" not in st.session_state:
    st.session_state.data = {
        "guest_name": "",
        "booking_number": "",
        "check_in": "",
        "check_out": "",
        "nights": "",
        "total_price": "",
        "channel": "Booking.com",
        "commissionable_amount": "",
        "commission_and_charges": "",
    }


if uploaded:
    col_img, col_extract = st.columns([1, 1])

    with col_img:
        st.image(
            uploaded,
            caption="Screenshot κράτησης",
            use_container_width=True,
        )

    with col_extract:
        if st.button(
            "Διάβασε αυτόματα από το screenshot",
            type="primary",
        ):
            parsed, err = extract_booking_from_image(uploaded)

            if err:
                st.warning(err)

            if parsed:
                st.session_state.data.update(parsed)
                st.success(
                    "Διαβάστηκαν τα στοιχεία. "
                    "Έλεγξέ τα πριν βγάλεις PDF."
                )


st.subheader("Έλεγχος / διόρθωση στοιχείων κράτησης")

col1, col2, col3 = st.columns(3)

with col1:
    st.session_state.data["guest_name"] = st.text_input(
        "Όνομα πελάτη",
        st.session_state.data.get("guest_name", ""),
    )

    st.session_state.data["booking_number"] = st.text_input(
        "Booking number",
        st.session_state.data.get("booking_number", ""),
    )

with col2:
    st.session_state.data["check_in"] = st.text_input(
        "Check-in",
        st.session_state.data.get("check_in", ""),
    )

    st.session_state.data["check_out"] = st.text_input(
        "Check-out",
        st.session_state.data.get("check_out", ""),
    )

with col3:
    st.session_state.data["nights"] = st.text_input(
        "Nights / Ημέρες",
        st.session_state.data.get("nights", ""),
    )

    st.session_state.data["total_price"] = st.text_input(
        "Total price",
        st.session_state.data.get("total_price", ""),
    )


check_in_fmt = parse_date_any(st.session_state.data.get("check_in"))
check_out_fmt = parse_date_any(st.session_state.data.get("check_out"))

nights_num = re.sub(
    r"[^0-9]",
    "",
    str(st.session_state.data.get("nights", "")),
)

nights_num = int(nights_num) if nights_num else 0

default_desc = (
    f"{nights_num}-night accommodation from {check_in_fmt} to {check_out_fmt}"
    if nights_num
    else ""
)

default_ref = (
    f"Booking.com reservation no. "
    f"{st.session_state.data.get('booking_number', '')}"
)


st.subheader("Πεδία που θα μπουν στο PDF")

col4, col5 = st.columns(2)

with col4:
    description = st.text_input(
        "Περιγραφή",
        default_desc,
    )

    booking_ref = st.text_input(
        "Booking Ref",
        default_ref,
    )

with col5:
    series = st.text_input(
        "Σειρά",
        "1",
    )

    document_number = st.text_input(
        "Αρ. Παραστατικού",
        "1",
    )

    document_date = st.text_input(
        "Ημερομηνία",
        datetime.today().strftime("%d/%m/%Y"),
    )


notes = st.text_input(
    "Αιτιολογία / Παρατηρήσεις",
    "",
)


options = {
    "include_stamp": include_stamp,
    "stamp_rate": stamp_rate,
    "vat_rate": vat_rate,
    "description": description,
    "booking_ref": booking_ref,
    "series": series,
    "document_number": document_number,
    "document_date": document_date,
    "notes": notes,
}


fields = build_invoice_fields(
    st.session_state.data,
    business,
    options,
)


st.subheader("Προεπισκόπηση υπολογισμών")

preview_cols = st.columns(5)

preview_cols[0].metric(
    "Καθαρή αξία",
    fields["net_amount"],
)

preview_cols[1].metric(
    "Χαρτόσημο",
    fields["stamp_duty"],
)

preview_cols[2].metric(
    "ΦΠΑ",
    fields["vat_amount"],
)

preview_cols[3].metric(
    "Πληρωτέο",
    fields["payable_amount"],
)

preview_cols[4].metric(
    "Ημέρες",
    fields["item1_qty"] or "-",
)

st.write(
    "**Ποσό ολογράφως:**",
    fields["amount_in_words"],
)


# Optional template upload
with st.expander("Προχωρημένο: άλλο PDF template"):
    template_upload = st.file_uploader(
        "Ανέβασε άλλο template PDF",
        type=["pdf"],
        key="template_upload",
    )


template_path = DEFAULT_TEMPLATE

if template_upload:
    temp_path = APP_DIR / "uploaded_template.pdf"
    temp_path.write_bytes(template_upload.getvalue())
    template_path = temp_path


if st.button(
    "Δημιουργία συμπληρωμένου PDF",
    type="primary",
):
    if not Path(template_path).exists():
        st.error("Δεν βρέθηκε το template_invoice.pdf.")
    else:
        pdf_bytes = fill_pdf(template_path, fields)

        filename = (
            f"apodeixi_booking_"
            f"{st.session_state.data.get('booking_number', '') or 'reservation'}"
            f".pdf"
        )

        st.success("Έτοιμο PDF.")

        st.download_button(
            "Κατέβασμα PDF",
            data=pdf_bytes,
            file_name=filename,
            mime="application/pdf",
        )


st.info(
    "Άνοιξε το τελικό PDF με Adobe Acrobat Reader "
    "για καλύτερη εμφάνιση των συμπληρωμένων πεδίων."
)