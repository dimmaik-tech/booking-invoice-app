BOOKING.COM -> ΑΠΟΔΕΙΞΗ PDF

1. Βάλε όλα τα αρχεία στον ίδιο φάκελο.
2. Άνοιξε PowerShell μέσα στον φάκελο.
3. Εγκατάσταση:
   python -m pip install -r requirements.txt
4. Βάλε το OpenAI API key:
   - Τοπικά σε Windows:
     setx OPENAI_API_KEY "το_key_σου"
   - Ή στο Streamlit Cloud -> Settings -> Secrets:
     OPENAI_API_KEY="το_key_σου"
5. Εκτέλεση:
   streamlit run booking_to_invoice_app.py

ΧΡΗΣΗ:
- Ανεβάζεις screenshot κράτησης Booking.com.
- Πατάς "Διάβασε αυτόματα από το screenshot".
- Ελέγχεις / διορθώνεις τα στοιχεία.
- Επιλέγεις αν θες χαρτόσημο και ποσοστό π.χ. 3,6.
- Πατάς "Δημιουργία συμπληρωμένου PDF".

ΣΗΜΕΙΩΣΗ:
Για καλύτερη εμφάνιση άνοιξε το τελικό PDF με Adobe Acrobat Reader.
