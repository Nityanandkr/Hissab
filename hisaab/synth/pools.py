"""Vocabulary for inventing FICTIONAL local shops and people.

Deliberately NOT the same lists as hisaab/seed/*.json: if the generator reused the app's own word lists, every test would
just be the code re-reading its own vocabulary. Here we include shop-owner nicknames (PAPPU, GUDDU...), unusual surnames
and shop words the lexicon has never seen, because real statements are full of them.
"""
from __future__ import annotations

DEITY = ["BALAJI", "GANESH", "DURGA", "LAXMI", "SAI", "SHIV", "RADHE", "HANUMAN", "KRISHNA", "MAA TARA", "JAGDAMBA",
         "BAJRANG", "OM SAI", "SHREE RAM", "ANNAPURNA", "MAHALAXMI", "BHOLENATH", "GAYATRI", "KALI", "NEELKANTH",
         "ADITYA", "PARAS", "AMBIKA", "SIDDHI", "VINAYAK", "JAI MATA DI", "KAMAKHYA", "TIRUPATI"]
PREFIX = ["NEW", "ROYAL", "SUPER", "MODERN", "CITY", "GOLDEN", "STAR", "SUNRISE", "UNITED", "NATIONAL", "LUCKY",
          "PERFECT", "FRIENDS", "ALL IN ONE", "DELUXE", "METRO", "CLASSIC", "PRIME", "SHREE", "SRI"]
PLACE = ["CAMPUS", "NAGAR", "MAIN ROAD", "STATION ROAD", "CITY", "GATE NO 2", "BUS STAND", "LAKE VIEW", "COLLEGE ROAD",
         "MARKET", "CHOWK", "HOSTEL GATE", "NEW COLONY", "RING ROAD", "RIVER ROAD", "COLLEGE GATE"]

# owners: nicknames + first names + surnames that are not (all) in seed/names.json
OWNER_FIRST = ["PAPPU", "GUDDU", "BABLU", "MUNNA", "RINKU", "PINTU", "CHHOTU", "BHOLA", "KALLU", "RAJU", "SONU", "MONU",
                "TINKU", "DABLU", "LALLU", "BITTU", "SHAMBHU", "RAMLAL", "SITARAM", "DWARIKA", "HIRALAL", "BHAGWAN",
                "CHANDU", "NANDU", "PRAKASH", "RAMESH", "SURESH", "MAHESH", "DINESH", "GANESH", "NARESH", "BRIJESH",
                "SATYENDRA", "RAMASHRAY", "JAGDISH", "KAILASH", "MURARI", "BAIJNATH", "SUKHDEO", "RAMCHANDRA"]
OWNER_LAST = ["GUPTA", "SHARMA", "YADAV", "SINGH", "PRASAD", "MAHTO", "KESHRI", "CHOURASIA", "VISHWAKARMA", "SAO", "BHAGAT",
              "KAPRI", "RAJAK", "KUMHAR", "TURI", "ORAON", "TIRKEY", "MUNDA", "PASWAN", "RAM", "THAKUR", "MODI", "SAHU",
              "MANDAL", "GHOSH", "DAS", "PAUL", "ROY", "JHA", "PANDEY"]

FRIEND_FIRST = ["AMAN", "RAHUL", "ROHAN", "KARAN", "ANKIT", "SHIVAM", "ABHAY", "NIKHIL", "HARSH", "YASH", "PRIYA", "NEHA",
                "ISHA", "ARJUN", "VIKAS", "SAURABH", "MOHD ASIF", "ZAID", "TANMAY", "DEEPAK", "SUMIT", "ANJALI", "RIYA", "KRITI"]
FRIEND_LAST = ["KUMAR", "VERMA", "SINGH", "SHARMA", "JHA", "PATEL", "ANSARI", "MISHRA", "GUPTA", "RAJ", "KHAN", "ROY",
               "SINHA", "TIWARI", "DUBEY", "CHAUHAN"]

# (category -> templates).  {O}=owner, {D}=deity, {P}=prefix, {L}=place
SHOP_TEMPLATES: dict[str, list[str]] = {
    "Groceries": ["{O} KIRANA STORE", "{O} GENERAL STORE", "{D} KIRANA", "{P} PROVISION STORE", "{O} & SONS GENERAL STORE",
                  "{O} SUPER MART", "{O} DAIRY", "{O} FRUIT CORNER", "{O} SABZI BHANDAR", "{L} DAIRY BOOTH", "{D} RATION SHOP",
                  "{O} FRESH VEG", "{P} SUPER SAVER"],
    "Food & Dining": ["{O} TEA STALL", "{D} CHAI TAPRI", "{O} SWEETS", "{O} BAKERY", "HOTEL {D}", "{P} DHABA", "{O} MOMOS CORNER",
                      "{O} JUICE CENTRE", "{L} CANTEEN", "{O} RESTAURANT", "{O} FAST FOOD", "{O} MISTHAN BHANDAR", "{D} BHOJNALAY",
                      "{P} TASTY BITES", "{O} LITTI CHOKHA", "{L} ROLL POINT", "{O} CHAT CORNER"],
    "Health & Medical": ["{D} MEDICAL HALL", "{O} MEDICAL STORE", "{O} CLINIC", "{L} PHARMACY", "DR {O} CLINIC", "{D} MEDICOS",
                         "{P} HEALTH CARE", "{L} DIAGNOSTIC CENTRE"],
    "Education": ["{L} XEROX", "{O} BOOK DEPOT", "{D} STATIONERS", "{O} PRINTING PRESS", "{L} CYBER CAFE", "{P} XEROX & PRINT",
                  "{O} COPY CENTRE"],
    "Shopping": ["{O} READYMADE GARMENTS", "{P} FASHION POINT", "{O} MOBILE POINT", "{O} ELECTRONICS", "{L} FOOTWEAR",
                 "{D} VASTRALAYA", "{O} COLLECTION", "{P} GIFT GALLERY", "{O} HANDLOOM"],
    "Personal Care": ["{O} GENTS SALON", "{P} UNISEX SALON", "{O} DRY CLEANERS", "{L} LAUNDRY", "{O} TAILORS", "{P} FITNESS CLUB",
                      "{O} HAIR CUT", "{D} IRON PRESS"],
    "Travel & Transport": ["{O} TRAVELS", "{L} FILLING STATION", "{O} PETROL PUMP", "{D} TRANSPORT", "{L} AUTO STAND"],
    "Bills & Recharge": ["{O} RECHARGE POINT", "{L} MOBILE RECHARGE", "{D} BROADBAND"],
    "Subscriptions & Entertainment": ["{L} GAME ZONE", "{P} CINEMA", "{O} GAMING CAFE"],
}
# deliberately ambiguous names: nothing in the name says what is sold
AMBIGUOUS_TEMPLATES = ["{O}", "{O} ENTERPRISES", "{O} & CO", "{P} TRADERS", "NEW {D}", "{D} AGENCY", "{O} BROTHERS", "{P} CORNER",
                       "{D} POINT", "{O} {O2}", "{I} TRADERS", "{O} STORES"]

# national brands that are NOT in seed/brands.json (so they look like "unknown merchants with brand-like names")
EXTRA_BRANDS = [("TACO BELL", "Food & Dining", ["TACO BELL", "YUM RESTAURANTS INDIA"]),
                ("BLUE TOKAI COFFEE", "Food & Dining", ["BLUE TOKAI", "BLUE TOKAI COFFEE ROASTERS"]),
                ("BATA", "Shopping", ["BATA INDIA", "BATA SHOE"]),
                ("FABINDIA", "Shopping", ["FABINDIA OVERSEAS", "FABINDIA"]),
                ("WAKEFIT", "Shopping", ["WAKEFIT INNOVATIONS", "WAKEFIT"]),
                ("PRINTO", "Education", ["PRINTO DOCUMENT SERVICES", "PRINTO"]),
                ("MUFTI", "Shopping", ["MUFTI", "KEWAL KIRAN CLOTHING"])]

BANK_CODES4 = ["YESB", "HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "PUNB", "BARB", "CNRB", "IDIB", "FDRL", "INDB"]
BANK_SHORT = ["Yes Bank", "State Bank Of I", "HDFC BANK", "ICICI Bank", "Axis Bank", "Kotak Mahindra", "Punjab National",
              "Bank of Baroda", "Canara Bank", "Federal Bank", "IndusInd Bank"]

REMARKS_GENERIC = ["Payment from PhonePe", "UPI", "Paid via Paytm", "Payment from Ph", "Sent using Paytm", "Pay", "Google Pay",
                   "UPI Payment", "Payment fr"]
REMARKS_P2P = ["food", "thanks", "lunch", "split", "dinner", "movie", "recharge", "bday treat", "chai", "return", "udhar",
               "bro", "mess", "rent share", "trip", "snacks", "books", "gift", ""]
