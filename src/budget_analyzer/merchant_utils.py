"""
Merchant normalization utilities for spending analysis.

This module provides functions to clean and categorize merchant descriptions
from credit card and bank statements.
"""

import csv
import os
import re


# =============================================================================
# BASELINE MERCHANT RULES
# Built-in rules for common merchants. User rules override these.
# =============================================================================
BASELINE_RULES = [
    # -------------------------------------------------------------------------
    # TRANSFERS & PAYMENTS (excluded from spending analysis)
    # -------------------------------------------------------------------------
    ('VENMO', 'Venmo', 'Transfers', 'P2P'),
    ('PAYPAL', 'PayPal', 'Transfers', 'P2P'),
    ('ZELLE', 'Zelle', 'Transfers', 'P2P'),
    ('CASH APP', 'Cash App', 'Transfers', 'P2P'),
    ('APPLE CASH', 'Apple Cash', 'Transfers', 'P2P'),
    (r'ONLINE.*PAYMENT|ONLINE BANKING PAYMENT', 'Online Payment', 'Transfers', 'CC Payment'),
    ('ONLINE BANKING TRANSFER', 'Online Banking Transfer', 'Transfers', 'Transfer'),
    (r'PAYMENT.*THANK YOU', 'Payment Thank You', 'Transfers', 'CC Payment'),
    ('AUTOMATIC PAYMENT', 'Automatic Payment', 'Transfers', 'CC Payment'),
    (r'AMERICAN EXPRESS.*ACH|AMEX.*PMT', 'AMEX Payment', 'Transfers', 'CC Payment'),
    (r'CHASE.*PAYMENT|CHASE CREDIT CRD', 'Chase Payment', 'Transfers', 'CC Payment'),
    (r'CAPITAL ONE.*PAYMENT', 'Capital One Payment', 'Transfers', 'CC Payment'),
    (r'DISCOVER.*PAYMENT', 'Discover Payment', 'Transfers', 'CC Payment'),
    (r'WIRE.*TRANSFER', 'Wire Transfer', 'Transfers', 'Wire'),
    (r'ACH.*TRANSFER', 'ACH Transfer', 'Transfers', 'ACH'),
    (r'BANK TRANSFER|FUNDS TRANSFER', 'Bank Transfer', 'Transfers', 'Transfer'),
    ('KEEP THE CHANGE', 'Keep The Change', 'Transfers', 'Savings'),

    # -------------------------------------------------------------------------
    # INCOME (excluded from spending analysis)
    # -------------------------------------------------------------------------
    (r'DIRECT DEPOSIT|DIR DEP', 'Direct Deposit', 'Income', 'Salary'),
    (r'PAYROLL|PAYCHECK', 'Payroll', 'Income', 'Salary'),
    ('TAX REFUND', 'Tax Refund', 'Income', 'Refund'),
    ('INTEREST PAYMENT', 'Interest', 'Income', 'Interest'),

    # -------------------------------------------------------------------------
    # TRAVEL - AIRLINES
    # -------------------------------------------------------------------------
    (r'ALASKA AIR|ALASKAAIR', 'Alaska Airlines', 'Travel', 'Airline'),
    (r'AMERICAN AIR|AA\.COM', 'American Airlines', 'Travel', 'Airline'),
    (r'UNITED AIR|UNITED\.COM', 'United Airlines', 'Travel', 'Airline'),
    (r'DELTA AIR|DELTA\.COM', 'Delta Air Lines', 'Travel', 'Airline'),
    (r'SOUTHWEST|SWABIZ', 'Southwest Airlines', 'Travel', 'Airline'),
    ('JETBLUE', 'JetBlue', 'Travel', 'Airline'),
    ('FRONTIER AIR', 'Frontier Airlines', 'Travel', 'Airline'),
    ('SPIRIT AIR', 'Spirit Airlines', 'Travel', 'Airline'),
    ('HAWAIIAN AIR', 'Hawaiian Airlines', 'Travel', 'Airline'),
    ('SUN COUNTRY', 'Sun Country', 'Travel', 'Airline'),
    (r'BRITISH AIR|BA\.COM', 'British Airways', 'Travel', 'Airline'),
    ('AIR FRANCE', 'Air France', 'Travel', 'Airline'),
    ('LUFTHANSA', 'Lufthansa', 'Travel', 'Airline'),
    (r'KLM\s', 'KLM', 'Travel', 'Airline'),
    ('EMIRATES', 'Emirates', 'Travel', 'Airline'),
    ('VIRGIN ATLANTIC', 'Virgin Atlantic', 'Travel', 'Airline'),
    ('CATHAY PACIFIC', 'Cathay Pacific', 'Travel', 'Airline'),
    ('SINGAPORE AIR', 'Singapore Airlines', 'Travel', 'Airline'),
    (r'JAPAN AIR|JAL\s', 'Japan Airlines', 'Travel', 'Airline'),
    (r'ANA\s.*AIR|ALL NIPPON', 'ANA', 'Travel', 'Airline'),
    ('QANTAS', 'Qantas', 'Travel', 'Airline'),
    ('AIR CANADA', 'Air Canada', 'Travel', 'Airline'),
    ('WESTJET', 'WestJet', 'Travel', 'Airline'),
    ('RYANAIR', 'Ryanair', 'Travel', 'Airline'),
    ('EASYJET', 'EasyJet', 'Travel', 'Airline'),
    ('NORWEGIAN AIR', 'Norwegian Air', 'Travel', 'Airline'),
    ('ICELANDAIR', 'Icelandair', 'Travel', 'Airline'),

    # -------------------------------------------------------------------------
    # TRAVEL - LODGING
    # -------------------------------------------------------------------------
    (r'MARRIOTT|BONVOY|WESTIN|SHERATON|W HOTEL|RITZ CARLTON|COURTYARD|RESIDENCE INN|SPRINGHILL', 'Marriott', 'Travel', 'Lodging'),
    (r'HILTON|HAMPTON INN|DOUBLETREE|EMBASSY SUITES|WALDORF|CONRAD|HOMEWOOD SUITES|HILTON GARDEN', 'Hilton', 'Travel', 'Lodging'),
    (r'HYATT|ANDAZ|PARK HYATT|HYATT PLACE|HYATT HOUSE', 'Hyatt', 'Travel', 'Lodging'),
    (r'IHG|HOLIDAY INN|CROWNE PLAZA|INTERCONTINENTAL|KIMPTON|STAYBRIDGE|CANDLEWOOD', 'IHG', 'Travel', 'Lodging'),
    ('BEST WESTERN', 'Best Western', 'Travel', 'Lodging'),
    (r'WYNDHAM|LA QUINTA|DAYS INN|RAMADA|SUPER 8|TRAVELODGE|MICROTEL|BAYMONT', 'Wyndham', 'Travel', 'Lodging'),
    (r'CHOICE HOTEL|COMFORT INN|QUALITY INN|SLEEP INN|CLARION|ASCEND|CAMBRIA', 'Choice Hotels', 'Travel', 'Lodging'),
    ('FOUR SEASONS', 'Four Seasons', 'Travel', 'Lodging'),
    ('FAIRMONT', 'Fairmont', 'Travel', 'Lodging'),
    (r'MOTEL 6|MOTEL6|STUDIO 6', 'Motel 6', 'Travel', 'Lodging'),
    ('RED ROOF', 'Red Roof Inn', 'Travel', 'Lodging'),
    ('EXTENDED STAY', 'Extended Stay', 'Travel', 'Lodging'),
    ('DRURY INN', 'Drury Inn', 'Travel', 'Lodging'),
    (r'OMNI HOTEL|OMNI\s', 'Omni Hotels', 'Travel', 'Lodging'),
    ('LOEWS HOTEL', 'Loews Hotels', 'Travel', 'Lodging'),
    ('MANDARIN ORIENTAL', 'Mandarin Oriental', 'Travel', 'Lodging'),
    ('PENINSULA HOTEL', 'The Peninsula', 'Travel', 'Lodging'),
    ('ST REGIS', 'St. Regis', 'Travel', 'Lodging'),
    (r'W HOTELS|W\s+HOTEL', 'W Hotels', 'Travel', 'Lodging'),
    ('AIRBNB', 'Airbnb', 'Travel', 'Lodging'),
    ('VRBO', 'VRBO', 'Travel', 'Lodging'),
    (r'SONDER\s', 'Sonder', 'Travel', 'Lodging'),
    (r'BOOKING\.COM', 'Booking.com', 'Travel', 'Booking'),
    (r'HOTELS\.COM', 'Hotels.com', 'Travel', 'Booking'),
    ('EXPEDIA', 'Expedia', 'Travel', 'Booking'),
    ('KAYAK', 'Kayak', 'Travel', 'Booking'),
    ('PRICELINE', 'Priceline', 'Travel', 'Booking'),
    ('TRAVELOCITY', 'Travelocity', 'Travel', 'Booking'),
    ('TRIPADVISOR', 'TripAdvisor', 'Travel', 'Booking'),

    # -------------------------------------------------------------------------
    # TRAVEL - CAR RENTAL
    # -------------------------------------------------------------------------
    ('AVIS RENT', 'Avis', 'Travel', 'Car Rental'),
    ('HERTZ', 'Hertz', 'Travel', 'Car Rental'),
    ('ENTERPRISE', 'Enterprise', 'Travel', 'Car Rental'),
    ('NATIONAL CAR', 'National', 'Travel', 'Car Rental'),
    ('BUDGET RENT', 'Budget', 'Travel', 'Car Rental'),
    ('ALAMO RENT', 'Alamo', 'Travel', 'Car Rental'),
    ('DOLLAR RENT', 'Dollar', 'Travel', 'Car Rental'),
    ('THRIFTY', 'Thrifty', 'Travel', 'Car Rental'),
    (r'SIXT\s', 'Sixt', 'Travel', 'Car Rental'),
    ('TURO', 'Turo', 'Travel', 'Car Rental'),
    ('ZIPCAR', 'Zipcar', 'Travel', 'Car Rental'),

    # -------------------------------------------------------------------------
    # TRAVEL - OTHER
    # -------------------------------------------------------------------------
    (r'PRIORITY PASS|LOUNGE', 'Airport Lounge', 'Travel', 'Lounge'),
    (r'TSA PRECHECK|GLOBAL ENTRY|CBP\s', 'TSA/Global Entry', 'Travel', 'TSA'),
    (r'CLEAR\s', 'Clear', 'Travel', 'TSA'),
    ('TRAVELEX', 'Travelex', 'Travel', 'Currency Exchange'),
    (r'GOGOAIR|GOGO\s|GOGO INFLIGHT', 'Gogo WiFi', 'Travel', 'WiFi'),
    (r'TRAVEL INSURANCE|ALLIANZ TRAVEL', 'Travel Insurance', 'Travel', 'Insurance'),

    # -------------------------------------------------------------------------
    # TRANSPORT - RIDESHARE
    # -------------------------------------------------------------------------
    (r'UBER\s(?!EATS)', 'Uber', 'Transport', 'Rideshare'),
    ('LYFT', 'Lyft', 'Transport', 'Rideshare'),
    (r'TAXI|CAB\s', 'Taxi', 'Transport', 'Taxi'),
    ('YELLOW CAB', 'Yellow Cab', 'Transport', 'Taxi'),

    # -------------------------------------------------------------------------
    # TRANSPORT - TRANSIT & TOLLS
    # -------------------------------------------------------------------------
    (r'MTA\s|METRO CARD', 'MTA', 'Transport', 'Transit'),
    (r'BART\s', 'BART', 'Transport', 'Transit'),
    (r'WMATA|DC METRO', 'DC Metro', 'Transport', 'Transit'),
    ('CLIPPER', 'Clipper Card', 'Transport', 'Transit'),
    (r'ORCA\s', 'ORCA Card', 'Transport', 'Transit'),
    (r'OYSTER|TFL\.GOV', 'London TfL', 'Transport', 'Transit'),
    (r'GOOD TO GO|WSDOT', 'WSDOT Tolls', 'Transport', 'Tolls'),
    (r'FASTRAK|FASTPASS', 'FasTrak', 'Transport', 'Tolls'),
    (r'E.?ZPASS', 'E-ZPass', 'Transport', 'Tolls'),
    (r'PARKING|PARKWHIZ|SPOTHERO', 'Parking', 'Transport', 'Parking'),

    # -------------------------------------------------------------------------
    # TRANSPORT - GAS
    # -------------------------------------------------------------------------
    (r'SHELL\s', 'Shell', 'Transport', 'Gas'),
    ('CHEVRON', 'Chevron', 'Transport', 'Gas'),
    (r'EXXON|MOBIL\s', 'Exxon Mobil', 'Transport', 'Gas'),
    (r'BP\s|AMPM\s', 'BP', 'Transport', 'Gas'),
    ('ARCO', 'ARCO', 'Transport', 'Gas'),
    (r'76\s|UNOCAL', '76 Gas', 'Transport', 'Gas'),
    (r'COSTCO\s*GAS', 'Costco Gas', 'Transport', 'Gas'),
    ('VALERO', 'Valero', 'Transport', 'Gas'),
    (r'MARATHON\s', 'Marathon', 'Transport', 'Gas'),
    ('SUNOCO', 'Sunoco', 'Transport', 'Gas'),
    ('CIRCLE K', 'Circle K', 'Transport', 'Gas'),
    (r'WAWA\s(?!MARKET)', 'Wawa', 'Transport', 'Gas'),
    ('SHEETZ', 'Sheetz', 'Transport', 'Gas'),
    (r'QUIKTRIP|QT\s', 'QuikTrip', 'Transport', 'Gas'),
    ('RACETRAC', 'RaceTrac', 'Transport', 'Gas'),
    (r'PILOT\s|FLYING J', 'Pilot/Flying J', 'Transport', 'Gas'),
    (r'LOVE.*S TRAVEL', "Love's", 'Transport', 'Gas'),
    (r'TESLA.*SUPERCHARGER|SUPERCHARGER', 'Tesla Supercharger', 'Transport', 'EV Charging'),
    (r'CHARGEPOINT|ELECTRIFY AMERICA|EVGO', 'EV Charging', 'Transport', 'EV Charging'),

    # -------------------------------------------------------------------------
    # TRANSPORT - AUTO SERVICE
    # -------------------------------------------------------------------------
    ('JIFFY LUBE', 'Jiffy Lube', 'Transport', 'Auto Service'),
    ('VALVOLINE', 'Valvoline', 'Transport', 'Auto Service'),
    ('FIRESTONE', 'Firestone', 'Transport', 'Auto Service'),
    ('GOODYEAR', 'Goodyear', 'Transport', 'Auto Service'),
    ('DISCOUNT TIRE', 'Discount Tire', 'Transport', 'Auto Service'),
    (r'MIDAS\s', 'Midas', 'Transport', 'Auto Service'),
    (r'UHAUL|U-HAUL', 'U-Haul', 'Transport', 'Rental'),

    # -------------------------------------------------------------------------
    # FOOD - DELIVERY (check before general restaurant patterns)
    # -------------------------------------------------------------------------
    ('DOORDASH', 'DoorDash', 'Food', 'Delivery'),
    (r'UBER\s*EATS', 'Uber Eats', 'Food', 'Delivery'),
    ('GRUBHUB', 'Grubhub', 'Food', 'Delivery'),
    ('POSTMATES', 'Postmates', 'Food', 'Delivery'),
    ('SEAMLESS', 'Seamless', 'Food', 'Delivery'),
    ('CAVIAR', 'Caviar', 'Food', 'Delivery'),
    ('INSTACART', 'Instacart', 'Food', 'Grocery Delivery'),
    ('SHIPT', 'Shipt', 'Food', 'Grocery Delivery'),
    (r'AMAZONFRESH|AMZN.*FRESH', 'Amazon Fresh', 'Food', 'Grocery Delivery'),

    # -------------------------------------------------------------------------
    # FOOD - FAST FOOD
    # -------------------------------------------------------------------------
    ('MCDONALD', "McDonald's", 'Food', 'Fast Food'),
    (r'CHICK.FIL.A|CHICKFILA', 'Chick-fil-A', 'Food', 'Fast Food'),
    ('CHIPOTLE', 'Chipotle', 'Food', 'Fast Food'),
    ('TACO BELL', 'Taco Bell', 'Food', 'Fast Food'),
    (r'WENDY|WENDYS', "Wendy's", 'Food', 'Fast Food'),
    ('BURGER KING', 'Burger King', 'Food', 'Fast Food'),
    (r'SUBWAY\s', 'Subway', 'Food', 'Fast Food'),
    ('PANDA EXPRESS', 'Panda Express', 'Food', 'Fast Food'),
    ('FIVE GUYS', 'Five Guys', 'Food', 'Fast Food'),
    (r'IN.N.OUT', 'In-N-Out', 'Food', 'Fast Food'),
    ('POPEYES', 'Popeyes', 'Food', 'Fast Food'),
    (r'KFC\s', 'KFC', 'Food', 'Fast Food'),
    ('SONIC DRIVE', 'Sonic', 'Food', 'Fast Food'),
    ('WHATABURGER', 'Whataburger', 'Food', 'Fast Food'),
    ('JACK IN THE BOX', 'Jack in the Box', 'Food', 'Fast Food'),
    (r"CARL.*S JR|HARDEES", "Carl's Jr/Hardee's", 'Food', 'Fast Food'),
    (r"ARBY.*S", "Arby's", 'Food', 'Fast Food'),
    ('DAIRY QUEEN', 'Dairy Queen', 'Food', 'Fast Food'),
    ('WINGSTOP', 'Wingstop', 'Food', 'Fast Food'),
    ('BUFFALO WILD', 'Buffalo Wild Wings', 'Food', 'Fast Food'),
    ("PAPA MURPHY", "Papa Murphy's", 'Food', 'Fast Food'),
    ('LITTLE CAESARS', 'Little Caesars', 'Food', 'Fast Food'),
    (r"DOMINO.*S PIZZA", "Domino's", 'Food', 'Fast Food'),
    ('PIZZA HUT', 'Pizza Hut', 'Food', 'Fast Food'),
    ("PAPA JOHN", "Papa John's", 'Food', 'Fast Food'),
    # More fast food
    ('CULVERS', "Culver's", 'Food', 'Fast Food'),
    ("RAISING CANE", "Raising Cane's", 'Food', 'Fast Food'),
    ("ZAXBY", "Zaxby's", 'Food', 'Fast Food'),
    ('COOK OUT', 'Cook Out', 'Food', 'Fast Food'),
    ('CHECKERS', 'Checkers', 'Food', 'Fast Food'),
    ("RALLY'S", "Rally's", 'Food', 'Fast Food'),
    ('DEL TACO', 'Del Taco', 'Food', 'Fast Food'),
    ('KRISPY KREME', 'Krispy Kreme', 'Food', 'Fast Food'),
    (r"DUNKIN.*DONUTS", "Dunkin'", 'Food', 'Fast Food'),
    ('AUNTIE ANNES', "Auntie Anne's", 'Food', 'Fast Food'),
    ('CINNABON', 'Cinnabon', 'Food', 'Fast Food'),
    ('LONG JOHN SILVER', "Long John Silver's", 'Food', 'Fast Food'),
    ("CAPTAIN D", "Captain D's", 'Food', 'Fast Food'),
    ('BOJANGLES', "Bojangles'", 'Food', 'Fast Food'),
    ('CHURCH.*CHICKEN', "Church's Chicken", 'Food', 'Fast Food'),
    ('WAWA MARKET', 'Wawa', 'Food', 'Fast Food'),
    ('SHEETZ', 'Sheetz', 'Food', 'Fast Food'),
    ('NOODLES', 'Noodles & Company', 'Food', 'Fast Food'),
    (r'CHOPT\s', 'Chopt', 'Food', 'Fast Casual'),
    ('TENDER GREENS', 'Tender Greens', 'Food', 'Fast Casual'),
    ('COSI', 'Cosi', 'Food', 'Fast Casual'),
    (r'BOSTON MARKET', 'Boston Market', 'Food', 'Fast Casual'),
    ('EL POLLO LOCO', 'El Pollo Loco', 'Food', 'Fast Casual'),
    ('BAJA FRESH', 'Baja Fresh', 'Food', 'Fast Casual'),
    ('CORNER BAKERY', 'Corner Bakery', 'Food', 'Fast Casual'),
    ('JASON.*DELI', "Jason's Deli", 'Food', 'Fast Casual'),
    ('MCALISTER', "McAlister's", 'Food', 'Fast Casual'),
    ('SCHLOTZSKY', "Schlotzsky's", 'Food', 'Fast Casual'),
    ('FAZOLI', "Fazoli's", 'Food', 'Fast Casual'),

    # -------------------------------------------------------------------------
    # FOOD - FAST CASUAL
    # -------------------------------------------------------------------------
    ('SWEETGREEN', 'Sweetgreen', 'Food', 'Fast Casual'),
    ('PANERA', 'Panera Bread', 'Food', 'Fast Casual'),
    ('SHAKE SHACK', 'Shake Shack', 'Food', 'Fast Casual'),
    (r'CAVA\s', 'Cava', 'Food', 'Fast Casual'),
    (r'NOODLES.*CO', 'Noodles & Company', 'Food', 'Fast Casual'),
    ("JERSEY MIKE", "Jersey Mike's", 'Food', 'Fast Casual'),
    ("JIMMY JOHN", "Jimmy John's", 'Food', 'Fast Casual'),
    ('FIREHOUSE SUB', 'Firehouse Subs', 'Food', 'Fast Casual'),
    ('PRET A MANGER', 'Pret A Manger', 'Food', 'Fast Casual'),
    ('MOD PIZZA', 'MOD Pizza', 'Food', 'Fast Casual'),
    ('BLAZE PIZZA', 'Blaze Pizza', 'Food', 'Fast Casual'),
    ('SMASHBURGER', 'Smashburger', 'Food', 'Fast Casual'),
    ('QDOBA', 'Qdoba', 'Food', 'Fast Casual'),
    (r"MOE.*S SOUTHWEST", "Moe's", 'Food', 'Fast Casual'),
    ('POTBELLY', 'Potbelly', 'Food', 'Fast Casual'),
    (r'JAMBA|JAMBA JUICE', 'Jamba', 'Food', 'Fast Casual'),
    ('TROPICAL SMOOTHIE', 'Tropical Smoothie', 'Food', 'Fast Casual'),

    # -------------------------------------------------------------------------
    # FOOD - COFFEE
    # -------------------------------------------------------------------------
    ('STARBUCKS', 'Starbucks', 'Food', 'Coffee'),
    ('DUNKIN', "Dunkin'", 'Food', 'Coffee'),
    (r"PEET.*S COFFEE", "Peet's Coffee", 'Food', 'Coffee'),
    ('DUTCH BROS', 'Dutch Bros', 'Food', 'Coffee'),
    ('BLUE BOTTLE', 'Blue Bottle', 'Food', 'Coffee'),
    ('PHILZ', 'Philz Coffee', 'Food', 'Coffee'),
    ('CARIBOU COFFEE', 'Caribou Coffee', 'Food', 'Coffee'),
    ('TIM HORTON', 'Tim Hortons', 'Food', 'Coffee'),
    ('COFFEE BEAN', 'Coffee Bean', 'Food', 'Coffee'),
    ('LA COLOMBE', 'La Colombe', 'Food', 'Coffee'),
    ('INTELLIGENTSIA', 'Intelligentsia', 'Food', 'Coffee'),
    ('CAFFE NERO', 'Caffè Nero', 'Food', 'Coffee'),

    # -------------------------------------------------------------------------
    # FOOD - RESTAURANTS (casual dining)
    # -------------------------------------------------------------------------
    ('OLIVE GARDEN', 'Olive Garden', 'Food', 'Restaurant'),
    ('CHEESECAKE FACTORY', 'Cheesecake Factory', 'Food', 'Restaurant'),
    ('RED ROBIN', 'Red Robin', 'Food', 'Restaurant'),
    ('APPLEBEE', "Applebee's", 'Food', 'Restaurant'),
    (r"CHILI.*S GRILL", "Chili's", 'Food', 'Restaurant'),
    (r'TGI FRIDAY|FRIDAYS', "TGI Friday's", 'Food', 'Restaurant'),
    ('OUTBACK', 'Outback Steakhouse', 'Food', 'Restaurant'),
    ('TEXAS ROADHOUSE', 'Texas Roadhouse', 'Food', 'Restaurant'),
    ('LONGHORN', 'LongHorn Steakhouse', 'Food', 'Restaurant'),
    ('CRACKER BARREL', 'Cracker Barrel', 'Food', 'Restaurant'),
    ('RED LOBSTER', 'Red Lobster', 'Food', 'Restaurant'),
    (r'IHOP\s', 'IHOP', 'Food', 'Restaurant'),
    (r"DENNY.*S", "Denny's", 'Food', 'Restaurant'),
    ('WAFFLE HOUSE', 'Waffle House', 'Food', 'Restaurant'),
    ('BOB EVANS', 'Bob Evans', 'Food', 'Restaurant'),
    ('FIRST WATCH', 'First Watch', 'Food', 'Restaurant'),
    (r'P\.F\. CHANG|PFCHANG', "P.F. Chang's", 'Food', 'Restaurant'),
    ('BENIHANA', 'Benihana', 'Food', 'Restaurant'),
    ('YARD HOUSE', 'Yard House', 'Food', 'Restaurant'),
    (r"BJ.*S RESTAURANT", "BJ's Restaurant", 'Food', 'Restaurant'),
    ('CALIFORNIA PIZZA', 'California Pizza Kitchen', 'Food', 'Restaurant'),
    ('THE CAPITAL GRILLE', 'Capital Grille', 'Food', 'Restaurant'),
    (r"RUTH.*S CHRIS", "Ruth's Chris", 'Food', 'Restaurant'),
    (r"MORTON.*S", "Morton's", 'Food', 'Restaurant'),
    ('FLEMINGS', "Fleming's", 'Food', 'Restaurant'),
    (r'NOBU\s', 'Nobu', 'Food', 'Restaurant'),
    ('DIN TAI FUNG', 'Din Tai Fung', 'Food', 'Restaurant'),

    # -------------------------------------------------------------------------
    # FOOD - GROCERY
    # -------------------------------------------------------------------------
    ("TRADER JOE", "Trader Joe's", 'Food', 'Grocery'),
    (r'COSTCO(?!\s*GAS)', 'Costco', 'Food', 'Grocery'),
    ('SAFEWAY', 'Safeway', 'Food', 'Grocery'),
    (r'WHOLE FOODS|WHOLEFDS', 'Whole Foods', 'Food', 'Grocery'),
    ('KROGER', 'Kroger', 'Food', 'Grocery'),
    ('PUBLIX', 'Publix', 'Food', 'Grocery'),
    (r'ALDI\s', 'Aldi', 'Food', 'Grocery'),
    ('WEGMANS', 'Wegmans', 'Food', 'Grocery'),
    (r'H.E.B\s|HEB\s', 'H-E-B', 'Food', 'Grocery'),
    ('SPROUTS', 'Sprouts', 'Food', 'Grocery'),
    ('FOOD LION', 'Food Lion', 'Food', 'Grocery'),
    ('ALBERTSONS', 'Albertsons', 'Food', 'Grocery'),
    (r'VONS\s', 'Vons', 'Food', 'Grocery'),
    ('RALPHS', 'Ralphs', 'Food', 'Grocery'),
    ('HARRIS TEETER', 'Harris Teeter', 'Food', 'Grocery'),
    (r'STOP.*SHOP', 'Stop & Shop', 'Food', 'Grocery'),
    (r'GIANT\s|GIANT FOOD|GIANT EAGLE', 'Giant', 'Food', 'Grocery'),
    ('MEIJER', 'Meijer', 'Food', 'Grocery'),
    ('WINCO', 'WinCo', 'Food', 'Grocery'),
    ('MARKET BASKET', 'Market Basket', 'Food', 'Grocery'),
    ('SHOPRITE', 'ShopRite', 'Food', 'Grocery'),
    ('FRED MEYER', 'Fred Meyer', 'Food', 'Grocery'),
    (r'QFC\s', 'QFC', 'Food', 'Grocery'),
    ('METROPOLITAN MARKET', 'Metropolitan Market', 'Food', 'Grocery'),
    # More US regional grocers
    ('HY-VEE', 'Hy-Vee', 'Food', 'Grocery'),
    ('PIGGLY WIGGLY', 'Piggly Wiggly', 'Food', 'Grocery'),
    ('INGLES', 'Ingles', 'Food', 'Grocery'),
    ('BI-LO', 'BI-LO', 'Food', 'Grocery'),
    ('WINN-DIXIE', 'Winn-Dixie', 'Food', 'Grocery'),
    ('SAVE-A-LOT', 'Save-A-Lot', 'Food', 'Grocery'),
    ('LIDL', 'Lidl', 'Food', 'Grocery'),
    ('SMART & FINAL', 'Smart & Final', 'Food', 'Grocery'),
    ('STATER BROS', 'Stater Bros', 'Food', 'Grocery'),
    ('FOODMAXX', 'FoodMaxx', 'Food', 'Grocery'),
    ('LUCKY', 'Lucky Supermarkets', 'Food', 'Grocery'),
    ('RALEY', "Raley's", 'Food', 'Grocery'),
    ('BEL AIR', 'Bel Air', 'Food', 'Grocery'),
    ('ACME MARKETS', 'Acme Markets', 'Food', 'Grocery'),
    ('JEWEL-OSCO', 'Jewel-Osco', 'Food', 'Grocery'),
    ('SHAW', "Shaw's", 'Food', 'Grocery'),
    ('HANNAFORD', 'Hannaford', 'Food', 'Grocery'),
    ('PRICE CHOPPER', 'Price Chopper', 'Food', 'Grocery'),
    # UK Grocers
    ('TESCO', 'Tesco', 'Food', 'Grocery'),
    ('SAINSBURY', "Sainsbury's", 'Food', 'Grocery'),
    ('WAITROSE', 'Waitrose', 'Food', 'Grocery'),
    (r'MARKS.*SPENCER.*FOOD|M&S FOOD', 'M&S Food', 'Food', 'Grocery'),
    (r'ASDA\s', 'Asda', 'Food', 'Grocery'),
    ('MORRISONS', 'Morrisons', 'Food', 'Grocery'),
    # Specialty & Organic
    ('FRESH MARKET', 'Fresh Market', 'Food', 'Grocery'),
    ('EARTH FARE', 'Earth Fare', 'Food', 'Grocery'),
    ('NATURAL GROCER', 'Natural Grocers', 'Food', 'Grocery'),
    ('BRISTOL FARMS', 'Bristol Farms', 'Food', 'Grocery'),
    ('GELSONS', "Gelson's", 'Food', 'Grocery'),
    ('CENTRAL MARKET', 'Central Market', 'Food', 'Grocery'),

    # -------------------------------------------------------------------------
    # SHOPPING - ONLINE
    # -------------------------------------------------------------------------
    (r'AMAZON\.COM|AMZN(?!.*FRESH)|AMAZON MARKE', 'Amazon', 'Shopping', 'Online'),
    (r'TARGET\.COM|TARGETCOM', 'Target.com', 'Shopping', 'Online'),
    ('ETSY', 'Etsy', 'Shopping', 'Online'),
    ('WAYFAIR', 'Wayfair', 'Shopping', 'Online'),
    (r'BESTBUY\.COM|BESTBUYCOM', 'Best Buy Online', 'Shopping', 'Online'),
    ('EBAY', 'eBay', 'Shopping', 'Online'),
    (r'WALMART\.COM', 'Walmart.com', 'Shopping', 'Online'),
    (r'CHEWY\.COM|CHEWY', 'Chewy', 'Shopping', 'Online'),
    ('SHEIN', 'Shein', 'Shopping', 'Online'),
    ('TEMU', 'Temu', 'Shopping', 'Online'),
    (r'ALIBABA|ALIEXPRESS', 'AliExpress', 'Shopping', 'Online'),
    ('OVERSTOCK', 'Overstock', 'Shopping', 'Online'),
    ('SHOPBOP', 'Shopbop', 'Shopping', 'Online'),
    ('ASOS', 'ASOS', 'Shopping', 'Online'),

    # -------------------------------------------------------------------------
    # SHOPPING - RETAIL
    # -------------------------------------------------------------------------
    (r'TARGET\s(?!\.COM)', 'Target', 'Shopping', 'Retail'),
    (r'WALMART(?!\.COM)', 'Walmart', 'Shopping', 'Retail'),
    (r"SAM.*S CLUB", "Sam's Club", 'Shopping', 'Retail'),
    (r"BJ.*S WHOLESALE", "BJ's Wholesale", 'Shopping', 'Retail'),
    ('DOLLAR TREE', 'Dollar Tree', 'Shopping', 'Retail'),
    ('DOLLAR GENERAL', 'Dollar General', 'Shopping', 'Retail'),
    ('FAMILY DOLLAR', 'Family Dollar', 'Shopping', 'Retail'),
    ('FIVE BELOW', 'Five Below', 'Shopping', 'Retail'),
    ('BIG LOTS', 'Big Lots', 'Shopping', 'Retail'),
    ('TUESDAY MORNING', 'Tuesday Morning', 'Shopping', 'Retail'),
    (r'TOYS.*R.*US', 'Toys R Us', 'Shopping', 'Retail'),
    ('PARTY CITY', 'Party City', 'Shopping', 'Retail'),
    ('MICHAELS', 'Michaels', 'Shopping', 'Crafts'),
    (r'JOANN|JO-ANN', 'Joann', 'Shopping', 'Crafts'),
    ('HOBBY LOBBY', 'Hobby Lobby', 'Shopping', 'Crafts'),
    (r'BARNES.*NOBLE', 'Barnes & Noble', 'Shopping', 'Retail'),
    (r'HUDSON.*NEWS|HUDSONNEWS', 'Hudson News', 'Shopping', 'Retail'),

    # -------------------------------------------------------------------------
    # SHOPPING - HOME IMPROVEMENT
    # -------------------------------------------------------------------------
    ('HOME DEPOT', 'Home Depot', 'Shopping', 'Home'),
    (r"LOWES|LOWE.*S", "Lowe's", 'Shopping', 'Home'),
    ('MENARDS', 'Menards', 'Shopping', 'Home'),
    ('ACE HARDWARE', 'Ace Hardware', 'Shopping', 'Home'),
    ('TRUE VALUE', 'True Value', 'Shopping', 'Home'),
    ('HARBOR FREIGHT', 'Harbor Freight', 'Shopping', 'Home'),
    ('IKEA', 'IKEA', 'Shopping', 'Home'),
    (r'BED BATH|BEDBATH', 'Bed Bath & Beyond', 'Shopping', 'Home'),
    ('POTTERY BARN', 'Pottery Barn', 'Shopping', 'Home'),
    (r'CRATE.*BARREL', 'Crate & Barrel', 'Shopping', 'Home'),
    ('WILLIAMS.SONOMA', 'Williams-Sonoma', 'Shopping', 'Home'),
    ('CONTAINER STORE', 'The Container Store', 'Shopping', 'Home'),
    ('WEST ELM', 'West Elm', 'Shopping', 'Home'),
    (r'RESTORATION HARDWARE|RH\s', 'Restoration Hardware', 'Shopping', 'Home'),
    (r'CB2\s', 'CB2', 'Shopping', 'Home'),
    ('SUR LA TABLE', 'Sur La Table', 'Shopping', 'Home'),
    (r'WORLD MARKET|COST PLUS', 'World Market', 'Shopping', 'Home'),
    (r'PIER 1|PIER ONE', 'Pier 1', 'Shopping', 'Home'),
    (r'AT HOME\s', 'At Home', 'Shopping', 'Home'),
    ('HOMEGOODS', 'HomeGoods', 'Shopping', 'Home'),
    # UK
    ('HARRODS', 'Harrods', 'Shopping', 'Retail'),
    (r'MARKS.*SPENCER', 'Marks & Spencer', 'Shopping', 'Retail'),
    ('JOHN LEWIS', 'John Lewis', 'Shopping', 'Retail'),
    ('SELFRIDGES', 'Selfridges', 'Shopping', 'Retail'),

    # -------------------------------------------------------------------------
    # SHOPPING - CLOTHING
    # -------------------------------------------------------------------------
    ('NORDSTROM', 'Nordstrom', 'Shopping', 'Clothing'),
    ('LULULEMON', 'Lululemon', 'Shopping', 'Clothing'),
    (r'GAP\s|GAPFACTORY', 'Gap', 'Shopping', 'Clothing'),
    (r'OLD NAVY|OLDNAVY', 'Old Navy', 'Shopping', 'Clothing'),
    ('BANANA REPUBLIC', 'Banana Republic', 'Shopping', 'Clothing'),
    ('ATHLETA', 'Athleta', 'Shopping', 'Clothing'),
    (r'ZARA\s', 'Zara', 'Shopping', 'Clothing'),
    (r'H.*M\.COM|HM\.COM|H.*M\s', 'H&M', 'Shopping', 'Clothing'),
    ('NIKE', 'Nike', 'Shopping', 'Clothing'),
    ('ADIDAS', 'Adidas', 'Shopping', 'Clothing'),
    ('UNDER ARMOUR', 'Under Armour', 'Shopping', 'Clothing'),
    ('UNIQLO', 'Uniqlo', 'Shopping', 'Clothing'),
    (r"MACY.*S", "Macy's", 'Shopping', 'Clothing'),
    (r"KOHLS|KOHL.*S", "Kohl's", 'Shopping', 'Clothing'),
    (r'JC\s*PENNEY|JCPENNEY', 'JCPenney', 'Shopping', 'Clothing'),
    ("DILLARD", "Dillard's", 'Shopping', 'Clothing'),
    (r'TJ.*MAXX|TJMAXX', 'TJ Maxx', 'Shopping', 'Clothing'),
    ('MARSHALLS', 'Marshalls', 'Shopping', 'Clothing'),
    (r'ROSS\s', 'Ross', 'Shopping', 'Clothing'),
    ('BURLINGTON', 'Burlington', 'Shopping', 'Clothing'),
    (r'NORDSTROM RACK|HAUTELOOK', 'Nordstrom Rack', 'Shopping', 'Clothing'),
    (r'DSW\s', 'DSW', 'Shopping', 'Clothing'),
    ('FAMOUS FOOTWEAR', 'Famous Footwear', 'Shopping', 'Clothing'),
    ('FOOT LOCKER', 'Foot Locker', 'Shopping', 'Clothing'),
    ('FINISH LINE', 'Finish Line', 'Shopping', 'Clothing'),
    ('ANTHROPOLOGIE', 'Anthropologie', 'Shopping', 'Clothing'),
    ('FREE PEOPLE', 'Free People', 'Shopping', 'Clothing'),
    ('URBAN OUTFITTER', 'Urban Outfitters', 'Shopping', 'Clothing'),
    (r'EXPRESS\s', 'Express', 'Shopping', 'Clothing'),
    (r'J\.?CREW|JCREW', 'J.Crew', 'Shopping', 'Clothing'),
    ('ANN TAYLOR', 'Ann Taylor', 'Shopping', 'Clothing'),
    (r'LOFT\s', 'Loft', 'Shopping', 'Clothing'),
    ("CHICO.*S", "Chico's", 'Shopping', 'Clothing'),
    ('TORY BURCH', 'Tory Burch', 'Shopping', 'Clothing'),
    ('KATE SPADE', 'Kate Spade', 'Shopping', 'Clothing'),
    (r'COACH\s', 'Coach', 'Shopping', 'Clothing'),
    ('MICHAEL KORS', 'Michael Kors', 'Shopping', 'Clothing'),
    ('FOREVER 21', 'Forever 21', 'Shopping', 'Clothing'),
    ('AMERICAN EAGLE', 'American Eagle', 'Shopping', 'Clothing'),
    ('ABERCROMBIE', 'Abercrombie', 'Shopping', 'Clothing'),
    ('HOLLISTER', 'Hollister', 'Shopping', 'Clothing'),
    (r"VICTORIA.*S SECRET|VICTORIA SECRET", "Victoria's Secret", 'Shopping', 'Clothing'),
    (r'BATH.*BODY', 'Bath & Body Works', 'Shopping', 'Clothing'),

    # -------------------------------------------------------------------------
    # SHOPPING - ELECTRONICS
    # -------------------------------------------------------------------------
    (r'APPLE STORE|APPLE\.COM(?!/BILL)', 'Apple Store', 'Shopping', 'Electronics'),
    (r'BEST BUY(?!\.COM)', 'Best Buy', 'Shopping', 'Electronics'),
    ('MICRO CENTER', 'Micro Center', 'Shopping', 'Electronics'),
    (r'B.*H PHOTO', 'B&H Photo', 'Shopping', 'Electronics'),
    ('GAMESTOP', 'GameStop', 'Shopping', 'Electronics'),
    ('STAPLES', 'Staples', 'Shopping', 'Office'),
    (r'OFFICE DEPOT|OFFICEMAX', 'Office Depot', 'Shopping', 'Office'),

    # -------------------------------------------------------------------------
    # SHOPPING - BEAUTY
    # -------------------------------------------------------------------------
    ('SEPHORA', 'Sephora', 'Shopping', 'Beauty'),
    ('ULTA', 'Ulta Beauty', 'Shopping', 'Beauty'),
    ('BLUEMERCURY', 'Bluemercury', 'Shopping', 'Beauty'),
    ('GLOSSIER', 'Glossier', 'Shopping', 'Beauty'),
    ('MAC COSMETIC', 'MAC', 'Shopping', 'Beauty'),
    (r'LUSH\s', 'Lush', 'Shopping', 'Beauty'),

    # -------------------------------------------------------------------------
    # SHOPPING - BABY & KIDS
    # -------------------------------------------------------------------------
    ('BUY BUY BABY', 'Buy Buy Baby', 'Shopping', 'Baby'),
    (r"CARTER.*S", "Carter's", 'Shopping', 'Baby'),
    ('OSHKOSH', 'OshKosh', 'Shopping', 'Baby'),
    ('GYMBOREE', 'Gymboree', 'Shopping', 'Baby'),
    (r"CHILDREN.*S PLACE", "Children's Place", 'Shopping', 'Baby'),
    ('POTTERY BARN KIDS', 'Pottery Barn Kids', 'Shopping', 'Baby'),
    (r'JANIE.*JACK', 'Janie and Jack', 'Shopping', 'Baby'),

    # -------------------------------------------------------------------------
    # SHOPPING - PET
    # -------------------------------------------------------------------------
    ('PETCO', 'Petco', 'Shopping', 'Pet'),
    ('PETSMART', 'PetSmart', 'Shopping', 'Pet'),

    # -------------------------------------------------------------------------
    # SHOPPING - JEWELRY & GIFTS
    # -------------------------------------------------------------------------
    ('TIFFANY', 'Tiffany & Co', 'Shopping', 'Jewelry'),
    ('ZALES', 'Zales', 'Shopping', 'Jewelry'),
    ('KAY JEWELERS', 'Kay Jewelers', 'Shopping', 'Jewelry'),
    ('JARED', 'Jared', 'Shopping', 'Jewelry'),
    ('BLUE NILE', 'Blue Nile', 'Shopping', 'Jewelry'),
    ('HALLMARK', 'Hallmark', 'Shopping', 'Gifts'),
    (r'1-800-FLOWERS|FLOWERS\.COM', '1-800-Flowers', 'Shopping', 'Gifts'),
    (r'FTD\s', 'FTD', 'Shopping', 'Gifts'),
    ('PAPYRUS', 'Papyrus', 'Shopping', 'Gifts'),

    # -------------------------------------------------------------------------
    # HEALTH - PHARMACY
    # -------------------------------------------------------------------------
    ('CVS', 'CVS Pharmacy', 'Health', 'Pharmacy'),
    ('WALGREENS', 'Walgreens', 'Health', 'Pharmacy'),
    ('RITE AID', 'Rite Aid', 'Health', 'Pharmacy'),
    ('DUANE READE', 'Duane Reade', 'Health', 'Pharmacy'),
    (r'BOOTS\s', 'Boots', 'Health', 'Pharmacy'),

    # -------------------------------------------------------------------------
    # HEALTH - FITNESS
    # -------------------------------------------------------------------------
    ('PLANET FITNESS', 'Planet Fitness', 'Health', 'Gym'),
    ('LA FITNESS', 'LA Fitness', 'Health', 'Gym'),
    ('24 HOUR FITNESS', '24 Hour Fitness', 'Health', 'Gym'),
    ('LIFETIME FITNESS', 'Life Time', 'Health', 'Gym'),
    ('EQUINOX', 'Equinox', 'Health', 'Gym'),
    (r'ORANGETHEORY|OTF\s', 'Orangetheory', 'Health', 'Gym'),
    (r'F45\s|F45 TRAINING', 'F45', 'Health', 'Gym'),
    ('CROSSFIT', 'CrossFit', 'Health', 'Gym'),
    ('ANYTIME FITNESS', 'Anytime Fitness', 'Health', 'Gym'),
    (r'CRUNCH\s', 'Crunch Fitness', 'Health', 'Gym'),
    (r"GOLD.*S GYM", "Gold's Gym", 'Health', 'Gym'),
    (r'YMCA|Y\.M\.C\.A', 'YMCA', 'Health', 'Gym'),
    ('PELOTON', 'Peloton', 'Health', 'Fitness'),
    ('CLASSPASS', 'ClassPass', 'Health', 'Fitness'),
    (r'MINDBODY|MIND BODY', 'Mindbody', 'Health', 'Fitness'),

    # -------------------------------------------------------------------------
    # HEALTH - MEDICAL
    # -------------------------------------------------------------------------
    ('KAISER', 'Kaiser', 'Health', 'Medical'),
    (r'BLUE CROSS|BLUECROSS|BCBS', 'Blue Cross', 'Health', 'Insurance'),
    ('AETNA', 'Aetna', 'Health', 'Insurance'),
    ('CIGNA', 'Cigna', 'Health', 'Insurance'),
    (r'UNITED HEALTH|UHC', 'United Healthcare', 'Health', 'Insurance'),
    ('ANTHEM', 'Anthem', 'Health', 'Insurance'),
    ('ONE MEDICAL', 'One Medical', 'Health', 'Medical'),
    ('ZOCDOC', 'Zocdoc', 'Health', 'Medical'),
    ('LENSCRAFTERS', 'LensCrafters', 'Health', 'Vision'),
    ('PEARLE VISION', 'Pearle Vision', 'Health', 'Vision'),
    ('WARBY PARKER', 'Warby Parker', 'Health', 'Vision'),

    # -------------------------------------------------------------------------
    # SUBSCRIPTIONS - STREAMING
    # -------------------------------------------------------------------------
    ('NETFLIX', 'Netflix', 'Subscriptions', 'Streaming'),
    ('SPOTIFY', 'Spotify', 'Subscriptions', 'Streaming'),
    (r'DISNEY.*PLUS|DISNEYPLUS', 'Disney+', 'Subscriptions', 'Streaming'),
    ('HULU', 'Hulu', 'Subscriptions', 'Streaming'),
    (r'HBO.*MAX|MAX\s', 'Max', 'Subscriptions', 'Streaming'),
    (r'YOUTUBE|GOOGLE\*YOUTUBE', 'YouTube Premium', 'Subscriptions', 'Streaming'),
    (r'APPLE\.COM/BILL|ITUNES', 'Apple Services', 'Subscriptions', 'Streaming'),
    (r'AMAZON.*PRIME|PRIME VIDEO', 'Amazon Prime', 'Subscriptions', 'Streaming'),
    (r'PARAMOUNT.*PLUS', 'Paramount+', 'Subscriptions', 'Streaming'),
    ('PEACOCK', 'Peacock', 'Subscriptions', 'Streaming'),
    ('AUDIBLE', 'Audible', 'Subscriptions', 'Streaming'),
    (r'SIRIUS|SIRIUSXM', 'SiriusXM', 'Subscriptions', 'Streaming'),
    ('PANDORA', 'Pandora', 'Subscriptions', 'Streaming'),
    ('APPLE MUSIC', 'Apple Music', 'Subscriptions', 'Streaming'),
    (r'ESPN\+|ESPN PLUS', 'ESPN+', 'Subscriptions', 'Streaming'),
    ('CRUNCHYROLL', 'Crunchyroll', 'Subscriptions', 'Streaming'),

    # -------------------------------------------------------------------------
    # SUBSCRIPTIONS - SOFTWARE & CLOUD
    # -------------------------------------------------------------------------
    ('GITHUB', 'GitHub', 'Subscriptions', 'Software'),
    (r'MICROSOFT\*|MSBILL', 'Microsoft', 'Subscriptions', 'Software'),
    ('ADOBE', 'Adobe', 'Subscriptions', 'Software'),
    (r'OPENAI|CHATGPT', 'OpenAI/ChatGPT', 'Subscriptions', 'Software'),
    (r'ANTHROPIC|CLAUDE', 'Anthropic/Claude', 'Subscriptions', 'Software'),
    (r'GOOGLE ONE|GOOGLE\s\*', 'Google One', 'Subscriptions', 'Software'),
    (r'ICLOUD|APPLE\.COM/BILL', 'iCloud', 'Subscriptions', 'Software'),
    ('DROPBOX', 'Dropbox', 'Subscriptions', 'Software'),
    (r'ZOOM\.US|ZOOM VIDEO', 'Zoom', 'Subscriptions', 'Software'),
    ('SLACK', 'Slack', 'Subscriptions', 'Software'),
    ('NOTION', 'Notion', 'Subscriptions', 'Software'),
    ('EVERNOTE', 'Evernote', 'Subscriptions', 'Software'),
    ('LASTPASS', 'LastPass', 'Subscriptions', 'Software'),
    ('1PASSWORD', '1Password', 'Subscriptions', 'Software'),
    ('NORDVPN', 'NordVPN', 'Subscriptions', 'Software'),
    ('EXPRESSVPN', 'ExpressVPN', 'Subscriptions', 'Software'),

    # -------------------------------------------------------------------------
    # SUBSCRIPTIONS - NEWS & MEDIA
    # -------------------------------------------------------------------------
    (r'NY\s*TIMES|NYTIMES', 'NY Times', 'Subscriptions', 'News'),
    (r'WASHINGTON POST|WAPO', 'Washington Post', 'Subscriptions', 'News'),
    (r'WALL STREET JOURNAL|WSJ', 'Wall Street Journal', 'Subscriptions', 'News'),
    ('THE ATLANTIC', 'The Atlantic', 'Subscriptions', 'News'),
    ('NEW YORKER', 'The New Yorker', 'Subscriptions', 'News'),
    ('ECONOMIST', 'The Economist', 'Subscriptions', 'News'),
    ('MEDIUM', 'Medium', 'Subscriptions', 'News'),
    ('SUBSTACK', 'Substack', 'Subscriptions', 'News'),

    # -------------------------------------------------------------------------
    # SUBSCRIPTIONS - OTHER
    # -------------------------------------------------------------------------
    (r'AMAZON.*MEMBERSHIP', 'Amazon Prime Membership', 'Subscriptions', 'Membership'),
    (r'COSTCO.*MEMBERSHIP', 'Costco Membership', 'Subscriptions', 'Membership'),
    (r"SAM.*S CLUB.*MEMBER", "Sam's Club Membership", 'Subscriptions', 'Membership'),
    (r'AAA\s|AMERICAN AUTO', 'AAA', 'Subscriptions', 'Membership'),
    ('LINKEDIN', 'LinkedIn', 'Subscriptions', 'Professional'),

    # -------------------------------------------------------------------------
    # BILLS - UTILITIES
    # -------------------------------------------------------------------------
    (r'ELECTRIC|POWER\s|ENERGY', 'Electric', 'Bills', 'Electric'),
    (r'GAS\s.*COMPANY|NATURAL GAS', 'Gas Utility', 'Bills', 'Gas'),
    (r'WATER\s|WATER DEPT', 'Water', 'Bills', 'Water'),
    (r'SEWER|SEWAGE', 'Sewer', 'Bills', 'Water'),
    (r'GARBAGE|WASTE|TRASH', 'Waste Management', 'Bills', 'Trash'),

    # -------------------------------------------------------------------------
    # BILLS - TELECOM
    # -------------------------------------------------------------------------
    (r'AT&T|^ATT\s', 'AT&T', 'Bills', 'Mobile'),
    ('VERIZON', 'Verizon', 'Bills', 'Mobile'),
    ('T.MOBILE', 'T-Mobile', 'Bills', 'Mobile'),
    ('SPRINT', 'Sprint', 'Bills', 'Mobile'),
    ('US CELLULAR', 'US Cellular', 'Bills', 'Mobile'),
    ('MINT MOBILE', 'Mint Mobile', 'Bills', 'Mobile'),
    ('VISIBLE', 'Visible', 'Bills', 'Mobile'),
    ('GOOGLE FI', 'Google Fi', 'Bills', 'Mobile'),
    (r'COMCAST|XFINITY', 'Comcast/Xfinity', 'Bills', 'Internet'),
    ('SPECTRUM', 'Spectrum', 'Bills', 'Internet'),
    ('COX COMM', 'Cox', 'Bills', 'Internet'),
    ('CENTURYLINK', 'CenturyLink', 'Bills', 'Internet'),
    ('FRONTIER COMM', 'Frontier', 'Bills', 'Internet'),
    (r'OPTIMUM|ALTICE', 'Optimum', 'Bills', 'Internet'),
    ('GOOGLE FIBER', 'Google Fiber', 'Bills', 'Internet'),
    ('STARLINK', 'Starlink', 'Bills', 'Internet'),

    # -------------------------------------------------------------------------
    # BILLS - INSURANCE
    # -------------------------------------------------------------------------
    ('ALLSTATE', 'Allstate', 'Bills', 'Insurance'),
    ('STATE FARM', 'State Farm', 'Bills', 'Insurance'),
    ('GEICO', 'Geico', 'Bills', 'Insurance'),
    ('PROGRESSIVE', 'Progressive', 'Bills', 'Insurance'),
    ('LIBERTY MUTUAL', 'Liberty Mutual', 'Bills', 'Insurance'),
    ('FARMERS INS', 'Farmers', 'Bills', 'Insurance'),
    ('NATIONWIDE', 'Nationwide', 'Bills', 'Insurance'),
    ('USAA', 'USAA', 'Bills', 'Insurance'),
    ('TRAVELERS', 'Travelers', 'Bills', 'Insurance'),
    ('AMERICAN FAMILY', 'American Family', 'Bills', 'Insurance'),
    ('ERIE INSURANCE', 'Erie', 'Bills', 'Insurance'),
    ('NORTHWESTERN MU', 'Northwestern Mutual', 'Bills', 'Life Insurance'),
    ('METLIFE', 'MetLife', 'Bills', 'Life Insurance'),
    ('PRUDENTIAL', 'Prudential', 'Bills', 'Life Insurance'),
    ('NEW YORK LIFE', 'New York Life', 'Bills', 'Life Insurance'),
    ('MASS MUTUAL', 'MassMutual', 'Bills', 'Life Insurance'),

    # -------------------------------------------------------------------------
    # BILLS - TAX (for Annual classification)
    # -------------------------------------------------------------------------
    (r'IRS\s|USATAXPYMT|US TREASURY', 'IRS', 'Bills', 'Tax'),
    (r'STATE TAX|DOR\s', 'State Tax', 'Bills', 'Tax'),
    (r'PROPERTY TAX|COUNTY TAX', 'Property Tax', 'Bills', 'Tax'),
    (r'H.*R BLOCK|HRBLOCK', 'H&R Block', 'Bills', 'Tax Prep'),
    (r'TURBOTAX|INTUIT.*TAX', 'TurboTax', 'Bills', 'Tax Prep'),
    ('JACKSON HEWITT', 'Jackson Hewitt', 'Bills', 'Tax Prep'),

    # -------------------------------------------------------------------------
    # BILLS - FINANCIAL SERVICES
    # -------------------------------------------------------------------------
    (r'BANK FEE|SERVICE CHARGE|MONTHLY FEE', 'Bank Fee', 'Bills', 'Bank Fees'),
    ('OVERDRAFT', 'Overdraft Fee', 'Bills', 'Bank Fees'),
    (r'ANNUAL.*FEE|MEMBERSHIP FEE', 'Annual Fee', 'Bills', 'Bank Fees'),
    ('WIRE FEE', 'Wire Fee', 'Bills', 'Bank Fees'),
    ('FOREIGN TRANSACTION', 'Foreign Transaction Fee', 'Bills', 'Bank Fees'),

    # -------------------------------------------------------------------------
    # BILLS - EDUCATION
    # -------------------------------------------------------------------------
    ('TUITION', 'Tuition', 'Bills', 'Education'),
    (r'STUDENT LOAN|NAVIENT|NELNET|GREAT LAKES|FEDLOAN', 'Student Loan', 'Bills', 'Education'),
    (r'COLLEGE BOARD|SAT|ACT\s', 'College Board', 'Bills', 'Education'),
    ('COURSERA', 'Coursera', 'Bills', 'Education'),
    ('UDEMY', 'Udemy', 'Bills', 'Education'),
    ('LINKEDIN LEARN', 'LinkedIn Learning', 'Bills', 'Education'),
    ('MASTERCLASS', 'MasterClass', 'Bills', 'Education'),
    ('SKILLSHARE', 'Skillshare', 'Bills', 'Education'),
    ('DUOLINGO', 'Duolingo', 'Bills', 'Education'),

    # -------------------------------------------------------------------------
    # BILLS - HOME
    # -------------------------------------------------------------------------
    (r'MORTGAGE|ROCKET MORTGAGE|QUICKEN LOANS', 'Mortgage', 'Bills', 'Mortgage'),
    (r'RENT\s|APARTMENT|PROPERTY MGMT', 'Rent', 'Bills', 'Rent'),
    (r'HOA\s|HOMEOWNER', 'HOA', 'Bills', 'HOA'),
    (r'ADT\s|RING\s|SIMPLISAFE', 'Security', 'Bills', 'Security'),
    (r'TERMINIX|ORKIN|PEST', 'Pest Control', 'Bills', 'Pest Control'),
    (r'TRUGREEN|LAWN', 'Lawn Care', 'Bills', 'Lawn'),
    (r'MAID|CLEANING SVC|MOLLY MAID', 'Cleaning Service', 'Bills', 'Cleaning'),

    # -------------------------------------------------------------------------
    # BILLS - OTHER
    # -------------------------------------------------------------------------
    (r'DMV|DEPT.*MOTOR|DOL\s', 'DMV', 'Bills', 'DMV'),
    (r'USPS|UPS\s|FEDEX', 'Shipping', 'Bills', 'Shipping'),

    # -------------------------------------------------------------------------
    # ENTERTAINMENT
    # -------------------------------------------------------------------------
    ('AMC THEATRE', 'AMC Theatres', 'Entertainment', 'Movies'),
    ('REGAL CINEMA', 'Regal Cinemas', 'Entertainment', 'Movies'),
    ('CINEMARK', 'Cinemark', 'Entertainment', 'Movies'),
    ('FANDANGO', 'Fandango', 'Entertainment', 'Movies'),
    (r'IPIC\s', 'iPic', 'Entertainment', 'Movies'),
    ('ALAMO DRAFTHOUSE', 'Alamo Drafthouse', 'Entertainment', 'Movies'),
    ('TICKETMASTER', 'Ticketmaster', 'Entertainment', 'Events'),
    ('STUBHUB', 'StubHub', 'Entertainment', 'Events'),
    ('SEATGEEK', 'SeatGeek', 'Entertainment', 'Events'),
    (r'AXSTICKETS|AXS\s', 'AXS', 'Entertainment', 'Events'),
    ('EVENTBRITE', 'Eventbrite', 'Entertainment', 'Events'),
    ('LIVE NATION', 'Live Nation', 'Entertainment', 'Events'),
    (r'DAVE.*BUSTER', "Dave & Buster's", 'Entertainment', 'Games'),
    ('TOPGOLF', 'Topgolf', 'Entertainment', 'Games'),
    (r'BOWLERO|BOWLING', 'Bowling', 'Entertainment', 'Games'),
    ('ESCAPE ROOM', 'Escape Room', 'Entertainment', 'Games'),
    (r'MUSEUM|SMITHSONIAN', 'Museum', 'Entertainment', 'Culture'),
    (r'ZOO\s', 'Zoo', 'Entertainment', 'Culture'),
    ('AQUARIUM', 'Aquarium', 'Entertainment', 'Culture'),
    (r'THEME PARK|SIX FLAGS|DISNEY.*PARK|UNIVERSAL STUDIO', 'Theme Park', 'Entertainment', 'Theme Park'),

    # -------------------------------------------------------------------------
    # PERSONAL SERVICES - HAIR & BEAUTY
    # -------------------------------------------------------------------------
    (r'GREAT CLIPS', 'Great Clips', 'Personal', 'Hair'),
    ('SUPERCUTS', 'Supercuts', 'Personal', 'Hair'),
    ('SPORTS CLIPS', 'Sport Clips', 'Personal', 'Hair'),
    ('COST CUTTERS', 'Cost Cutters', 'Personal', 'Hair'),
    ('FANTASTIC SAMS', 'Fantastic Sams', 'Personal', 'Hair'),
    ('HAIR CUTTERY', 'Hair Cuttery', 'Personal', 'Hair'),
    ('FLOYD.*BARBERSHOP', "Floyd's Barbershop", 'Personal', 'Hair'),
    ('BIRDS BARBERSHOP', 'Birds Barbershop', 'Personal', 'Hair'),
    (r'SALON\s|HAIR\s*SALON', 'Hair Salon', 'Personal', 'Hair'),
    (r'BARBER\s', 'Barber', 'Personal', 'Hair'),
    (r'MASSAGE\s*ENVY', 'Massage Envy', 'Personal', 'Spa'),
    (r'HAND.*STONE', 'Hand & Stone', 'Personal', 'Spa'),
    ('ELEMENTS MASSAGE', 'Elements Massage', 'Personal', 'Spa'),
    (r'SPA\s', 'Spa', 'Personal', 'Spa'),
    ('EUROPEAN WAX', 'European Wax Center', 'Personal', 'Spa'),
    (r'NAIL\s*SALON|NAILS\s', 'Nail Salon', 'Personal', 'Nails'),
    ('DRYBAR', 'Drybar', 'Personal', 'Hair'),

    # -------------------------------------------------------------------------
    # PERSONAL SERVICES - DRY CLEANING & LAUNDRY
    # -------------------------------------------------------------------------
    (r'DRY CLEAN|CLEANERS', 'Dry Cleaner', 'Personal', 'Dry Cleaning'),
    ('MARTINIZING', 'Martinizing', 'Personal', 'Dry Cleaning'),
    ('ZIPS DRY', 'Zips Dry Cleaners', 'Personal', 'Dry Cleaning'),
    (r'LAUNDROMAT|LAUNDRY', 'Laundromat', 'Personal', 'Laundry'),

    # -------------------------------------------------------------------------
    # SPORTS & OUTDOORS
    # -------------------------------------------------------------------------
    (r'REI\s', 'REI', 'Shopping', 'Sports'),
    (r"DICK.*S\s*SPORTING", "Dick's Sporting Goods", 'Shopping', 'Sports'),
    ('BASS PRO', 'Bass Pro Shops', 'Shopping', 'Sports'),
    ('CABELAS', "Cabela's", 'Shopping', 'Sports'),
    ('ACADEMY SPORTS', 'Academy Sports', 'Shopping', 'Sports'),
    ('BIG 5 SPORTING', 'Big 5 Sporting', 'Shopping', 'Sports'),
    ('SPORTSMANS WAREHOUSE', "Sportsman's Warehouse", 'Shopping', 'Sports'),
    (r'SCHEELS\s', 'Scheels', 'Shopping', 'Sports'),
    ('SIERRA TRADING', 'Sierra', 'Shopping', 'Sports'),
    ('MOOSEJAW', 'Moosejaw', 'Shopping', 'Sports'),
    ('BACKCOUNTRY', 'Backcountry', 'Shopping', 'Sports'),
    ('PATAGONIA', 'Patagonia', 'Shopping', 'Sports'),
    ('THE NORTH FACE', 'The North Face', 'Shopping', 'Sports'),
    ('COLUMBIA SPORT', 'Columbia', 'Shopping', 'Sports'),
    (r'GOLF\s*GALAXY|GOLF\s*SMITH', 'Golf Galaxy', 'Shopping', 'Sports'),
    (r'PGA\s*TOUR|CALLAWAY', 'Golf', 'Shopping', 'Sports'),

    # -------------------------------------------------------------------------
    # AUTO PARTS
    # -------------------------------------------------------------------------
    ('AUTOZONE', 'AutoZone', 'Transport', 'Auto Parts'),
    (r"O.*REILLY AUTO", "O'Reilly Auto", 'Transport', 'Auto Parts'),
    (r'NAPA\s|NAPA AUTO', 'NAPA Auto Parts', 'Transport', 'Auto Parts'),
    ('ADVANCE AUTO', 'Advance Auto Parts', 'Transport', 'Auto Parts'),
    ('PEPBOYS', 'Pep Boys', 'Transport', 'Auto Parts'),
    ('CARQUEST', 'Carquest', 'Transport', 'Auto Parts'),

    # -------------------------------------------------------------------------
    # AUTO WASH
    # -------------------------------------------------------------------------
    (r'CAR\s*WASH|CARWASH', 'Car Wash', 'Transport', 'Car Wash'),
    ('MISTER CAR WASH', 'Mister Car Wash', 'Transport', 'Car Wash'),
    ('TAKE 5 CAR WASH', 'Take 5 Car Wash', 'Transport', 'Car Wash'),
    ('QUICK QUACK', 'Quick Quack', 'Transport', 'Car Wash'),
    ('ZIPS CAR WASH', 'Zips Car Wash', 'Transport', 'Car Wash'),

    # -------------------------------------------------------------------------
    # LIQUOR & WINE
    # -------------------------------------------------------------------------
    (r'TOTAL WINE|TOTALWINE', 'Total Wine', 'Shopping', 'Liquor'),
    ('BEV MO', 'BevMo', 'Shopping', 'Liquor'),
    ('SPEC.*S LIQUOR', "Spec's", 'Shopping', 'Liquor'),
    ('ABC LIQUOR', 'ABC Liquor', 'Shopping', 'Liquor'),
    (r'LIQUOR\s|WINE\s*SHOP', 'Liquor Store', 'Shopping', 'Liquor'),

    # -------------------------------------------------------------------------
    # BREWERIES & BARS
    # -------------------------------------------------------------------------
    (r'BREWERY|BREWING', 'Brewery', 'Food', 'Bar'),
    (r'TAPROOM|TAP\s*ROOM', 'Taproom', 'Food', 'Bar'),
    (r'PUB\s|IRISH\s*PUB', 'Pub', 'Food', 'Bar'),
    (r'BAR\s*&\s*GRILL', 'Bar & Grill', 'Food', 'Restaurant'),
    (r'SPORTS\s*BAR', 'Sports Bar', 'Food', 'Bar'),

    # -------------------------------------------------------------------------
    # MORE RESTAURANTS - CASUAL DINING
    # -------------------------------------------------------------------------
    ('GOLDEN CORRAL', 'Golden Corral', 'Food', 'Restaurant'),
    ('RUBY TUESDAY', 'Ruby Tuesday', 'Food', 'Restaurant'),
    (r"CARRABBA", "Carrabba's", 'Food', 'Restaurant'),
    ('BONEFISH GRILL', 'Bonefish Grill', 'Food', 'Restaurant'),
    ("BAHAMA BREEZE", "Bahama Breeze", 'Food', 'Restaurant'),
    (r'SEASONS 52', 'Seasons 52', 'Food', 'Restaurant'),
    ('MAGGIANOS', "Maggiano's", 'Food', 'Restaurant'),
    (r"MACARONI GRILL", "Macaroni Grill", 'Food', 'Restaurant'),
    (r"ON THE BORDER", "On The Border", 'Food', 'Restaurant'),
    (r"CHEDDAR.*S", "Cheddar's", 'Food', 'Restaurant'),
    (r'HOOTERS', 'Hooters', 'Food', 'Restaurant'),
    (r'TWIN PEAKS', 'Twin Peaks', 'Food', 'Restaurant'),
    (r"MILLER.*S ALE", "Miller's Ale House", 'Food', 'Restaurant'),
    (r'SIZZLER', 'Sizzler', 'Food', 'Restaurant'),

    # -------------------------------------------------------------------------
    # MORE COFFEE - TEA
    # -------------------------------------------------------------------------
    (r'TEAVANA|DAVIDSTEA', 'Tea Shop', 'Food', 'Coffee'),
    ('BOBA', 'Boba Tea', 'Food', 'Coffee'),
    ('KUNG FU TEA', 'Kung Fu Tea', 'Food', 'Coffee'),
    ('GONG CHA', 'Gong Cha', 'Food', 'Coffee'),
    (r'COFFEE\s*BEAN', 'Coffee Bean & Tea Leaf', 'Food', 'Coffee'),
    ('SCOOTERS COFFEE', "Scooter's Coffee", 'Food', 'Coffee'),
    ('BIGGBY', 'Biggby Coffee', 'Food', 'Coffee'),
    ('GREGORYS COFFEE', "Gregory's Coffee", 'Food', 'Coffee'),

    # -------------------------------------------------------------------------
    # ICE CREAM & DESSERTS
    # -------------------------------------------------------------------------
    ('BASKIN ROBBINS', 'Baskin-Robbins', 'Food', 'Dessert'),
    ('COLD STONE', 'Cold Stone', 'Food', 'Dessert'),
    (r"BEN.*JERRY", "Ben & Jerry's", 'Food', 'Dessert'),
    ('HAAGEN DAZS', 'Häagen-Dazs', 'Food', 'Dessert'),
    ('INSOMNIA COOKIES', 'Insomnia Cookies', 'Food', 'Dessert'),
    ('CRUMBL', 'Crumbl Cookies', 'Food', 'Dessert'),
    ('NOTHING BUNDT', 'Nothing Bundt Cakes', 'Food', 'Dessert'),
    ('SPRINKLES', 'Sprinkles', 'Food', 'Dessert'),
    (r'FROYO|YOGURTLAND|MENCHIES', 'Frozen Yogurt', 'Food', 'Dessert'),
    ('JAMBA', 'Jamba Juice', 'Food', 'Coffee'),
    (r'PINKBERRY|RED\s*MANGO', 'Frozen Yogurt', 'Food', 'Dessert'),

    # -------------------------------------------------------------------------
    # EDUCATION & TUTORING
    # -------------------------------------------------------------------------
    ('KUMON', 'Kumon', 'Bills', 'Education'),
    ('SYLVAN LEARNING', 'Sylvan Learning', 'Bills', 'Education'),
    ('MATHNASIUM', 'Mathnasium', 'Bills', 'Education'),
    ('HUNTINGTON LEARNING', 'Huntington', 'Bills', 'Education'),
    ('KAPLAN', 'Kaplan', 'Bills', 'Education'),
    ('PRINCETON REVIEW', 'Princeton Review', 'Bills', 'Education'),
    ('KHAN ACADEMY', 'Khan Academy', 'Bills', 'Education'),
    ('CODECADEMY', 'Codecademy', 'Bills', 'Education'),

    # -------------------------------------------------------------------------
    # CHILDCARE & KIDS ACTIVITIES
    # -------------------------------------------------------------------------
    ('KINDERCARE', 'KinderCare', 'Personal', 'Childcare'),
    ('BRIGHT HORIZONS', 'Bright Horizons', 'Personal', 'Childcare'),
    ('LITTLE GYM', 'The Little Gym', 'Personal', 'Kids Activities'),
    ('MY GYM', 'My Gym', 'Personal', 'Kids Activities'),
    ('GOLDFISH SWIM', 'Goldfish Swim School', 'Personal', 'Kids Activities'),
    ('GYMBOREE', 'Gymboree Play & Music', 'Personal', 'Kids Activities'),
    (r'DAYCARE|DAY\s*CARE', 'Daycare', 'Personal', 'Childcare'),

    # -------------------------------------------------------------------------
    # HOME SERVICES
    # -------------------------------------------------------------------------
    (r'PLUMBER|PLUMBING', 'Plumber', 'Bills', 'Home Services'),
    (r'ELECTRICIAN|ELECTRIC\s*SVC', 'Electrician', 'Bills', 'Home Services'),
    (r'HVAC|HEATING.*COOLING|AIR\s*COND', 'HVAC', 'Bills', 'Home Services'),
    (r'ROTO\s*ROOTER', 'Roto-Rooter', 'Bills', 'Home Services'),
    (r'HANDYMAN|HOME\s*REPAIR', 'Handyman', 'Bills', 'Home Services'),
    (r'STANLEY\s*STEEMER', 'Stanley Steemer', 'Bills', 'Home Services'),
    (r'SERVPRO', 'Servpro', 'Bills', 'Home Services'),
    (r'MOLLY\s*MAID', 'Molly Maid', 'Bills', 'Home Services'),
    (r'MERRY\s*MAIDS', 'Merry Maids', 'Bills', 'Home Services'),

    # -------------------------------------------------------------------------
    # STORAGE
    # -------------------------------------------------------------------------
    ('PUBLIC STORAGE', 'Public Storage', 'Bills', 'Storage'),
    ('EXTRA SPACE', 'Extra Space Storage', 'Bills', 'Storage'),
    ('CUBESMART', 'CubeSmart', 'Bills', 'Storage'),
    ('LIFE STORAGE', 'Life Storage', 'Bills', 'Storage'),
    (r'SELF\s*STORAGE|MINI\s*STORAGE', 'Self Storage', 'Bills', 'Storage'),

    # -------------------------------------------------------------------------
    # CASH & ATM
    # -------------------------------------------------------------------------
    (r'ATM.*WITHDRAW|ATM\s|CASH\s*WITHDRAW', 'ATM Withdrawal', 'Cash', 'ATM'),
    (r'^CHECK\s+\d+$', 'Check', 'Cash', 'Check'),
]


def load_merchant_rules(csv_path):
    """Load user merchant categorization rules from CSV file.

    CSV format: Pattern,Merchant,Category,Subcategory

    Lines starting with # are treated as comments and skipped.
    Patterns are Python regular expressions matched against transaction descriptions.

    Returns list of tuples: (pattern, merchant_name, category, subcategory)
    """
    if not os.path.exists(csv_path):
        return []  # No user rules file, just use baseline

    rules = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        # Filter out comment and empty lines before passing to DictReader
        lines = [line for line in f if line.strip() and not line.strip().startswith('#')]
        reader = csv.DictReader(lines)
        for row in reader:
            # Skip empty patterns
            if not row.get('Pattern', '').strip():
                continue
            rules.append((
                row['Pattern'],
                row['Merchant'],
                row['Category'],
                row['Subcategory']
            ))
    return rules


def get_all_rules(csv_path=None):
    """Get combined merchant rules: user rules first (override), then baseline.

    Args:
        csv_path: Optional path to user's merchant_categories.csv

    Returns:
        List of (pattern, merchant, category, subcategory) tuples.
        User rules come first so they take priority over baseline.
    """
    user_rules = []
    if csv_path:
        user_rules = load_merchant_rules(csv_path)

    # User rules first (checked first, can override baseline)
    return user_rules + list(BASELINE_RULES)


def diagnose_rules(csv_path=None):
    """Get detailed diagnostic information about rule loading.

    Returns a dict with:
        - baseline_count: Number of built-in baseline rules
        - user_rules_path: Path to user rules file (or None)
        - user_rules_exists: Whether the user rules file exists
        - user_rules_count: Number of user rules loaded
        - user_rules: List of user rules (pattern, merchant, category, subcategory)
        - user_rules_errors: List of any errors encountered while loading
        - total_rules: Total combined rules count
        - sample_baseline: First few baseline rules for reference
    """
    import re

    result = {
        'baseline_count': len(BASELINE_RULES),
        'user_rules_path': csv_path,
        'user_rules_exists': False,
        'user_rules_count': 0,
        'user_rules': [],
        'user_rules_errors': [],
        'total_rules': len(BASELINE_RULES),
        'sample_baseline': list(BASELINE_RULES[:5]),
    }

    if not csv_path:
        return result

    result['user_rules_exists'] = os.path.exists(csv_path)

    if not result['user_rules_exists']:
        return result

    # Load user rules with detailed error tracking
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            result['file_size_bytes'] = len(raw_content)
            result['file_lines'] = raw_content.count('\n') + 1

        with open(csv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            non_comment_lines = [line for line in lines if not line.strip().startswith('#') and line.strip()]
            result['non_comment_lines'] = len(non_comment_lines)

            # Check for header
            if non_comment_lines:
                first_line = non_comment_lines[0].strip()
                if 'Pattern' in first_line and 'Merchant' in first_line:
                    result['has_header'] = True
                else:
                    result['has_header'] = False
                    result['user_rules_errors'].append(
                        f"Missing or invalid header. Expected 'Pattern,Merchant,Category,Subcategory', got: {first_line[:50]}"
                    )

        # Now load rules with validation
        with open(csv_path, 'r', encoding='utf-8') as f:
            # Filter out comments AND empty lines
            lines = [line for line in f if line.strip() and not line.strip().startswith('#')]
            reader = csv.DictReader(lines)

            row_num = 1  # Start after header
            for row in reader:
                row_num += 1
                pattern = row.get('Pattern', '').strip()

                if not pattern:
                    continue  # Skip empty patterns silently

                # Validate the regex pattern
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Invalid regex pattern '{pattern}': {e}"
                    )
                    continue

                merchant = row.get('Merchant', '').strip()
                category = row.get('Category', '').strip()
                subcategory = row.get('Subcategory', '').strip()

                if not merchant:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Missing merchant name for pattern '{pattern}'"
                    )
                if not category:
                    result['user_rules_errors'].append(
                        f"Row {row_num}: Missing category for pattern '{pattern}'"
                    )

                result['user_rules'].append((pattern, merchant, category, subcategory))

        result['user_rules_count'] = len(result['user_rules'])
        result['total_rules'] = result['user_rules_count'] + result['baseline_count']

    except Exception as e:
        result['user_rules_errors'].append(f"Failed to read file: {e}")

    return result


def clean_description(description):
    """Clean and normalize raw transaction descriptions.

    Handles common prefixes, suffixes, and formatting issues that
    can't be represented in simple pattern matching rules.
    """
    cleaned = description

    # Remove common payment processor prefixes
    prefixes = [
        r'^APLPAY\s+',      # Apple Pay
        r'^AplPay\s+',      # Apple Pay (alternate case)
        r'^SQ\s*\*',        # Square
        r'^TST\*\s*',       # Toast POS
        r'^SP\s+',          # Shopify
        r'^PY\s*\*',        # PayPal merchant
        r'^PP\s*\*',        # PayPal
        r'^GOOGLE\s*\*',    # Google Pay (but keep for YouTube matching)
        r'^BT\s*\*?\s*DD\s*\*?',  # DoorDash via various processors
    ]

    for prefix in prefixes:
        cleaned = re.sub(prefix, '', cleaned, flags=re.IGNORECASE)

    # Remove BOA statement suffixes (ID numbers, confirmation codes)
    cleaned = re.sub(r'\s+DES:.*$', '', cleaned)
    cleaned = re.sub(r'\s+ID:.*$', '', cleaned)
    cleaned = re.sub(r'\s+INDN:.*$', '', cleaned)
    cleaned = re.sub(r'\s+CO ID:.*$', '', cleaned)
    cleaned = re.sub(r'\s+Confirmation#.*$', '', cleaned, flags=re.IGNORECASE)

    # Remove trailing location info (City, State format)
    # But be careful not to remove too much
    cleaned = re.sub(r'\s{2,}[A-Z]{2}$', '', cleaned)  # Trailing state code

    # Normalize whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


def extract_merchant_name(description):
    """Extract a readable merchant name from a cleaned description.

    Used as fallback when no pattern matches.
    """
    cleaned = clean_description(description)

    # Remove non-alphabetic characters for grouping, keep first 2-3 words
    words = re.sub(r'[^A-Za-z\s]', ' ', cleaned).split()[:3]

    if words:
        return ' '.join(words).title()
    return 'Unknown'


def apply_special_transformations(description):
    """Apply special merchant transformations that can't be expressed as simple patterns.

    Args:
        description: Raw transaction description

    Returns (merchant_name, category, subcategory) if a special rule matches,
    or None if no special rule applies.
    """
    desc_upper = description.upper()

    # =========================================================================
    # LOCATION-BASED FIXES
    # Handle merchants that might be confused with location names
    # =========================================================================

    # "KAHULUI" (Maui city) should NOT match "HULU"
    if 'KAHULUI' in desc_upper and 'HULU' not in desc_upper.replace('KAHULUI', ''):
        # This is Hawaii travel, not Hulu streaming
        return None  # Let it fall through to other patterns

    # "SEATTLE" contains "ATT" but is not AT&T
    if 'SEATTLE' in desc_upper and not desc_upper.startswith('AT&T') and not desc_upper.startswith('ATT*'):
        # Check if this is actually AT&T
        if not re.search(r'^AT&T|^ATT\s|ATT\*BILL', desc_upper):
            return None  # Not AT&T, let other patterns handle it

    # =========================================================================
    # MERCHANT NAME CONSOLIDATION
    # Combine variations of the same merchant
    # =========================================================================

    # Metropolitan Market has multiple formats
    if 'METROPOLITAN' in desc_upper and ('MARKET' in desc_upper or 'KIRKLAND' in desc_upper):
        return ('Metropolitan Market', 'Food', 'Grocery')

    # Din Tai Fung variations
    if 'DIN TAI' in desc_upper or 'DINTAI' in desc_upper:
        return ('Din Tai Fung', 'Food', 'Restaurant')

    # Gap family of brands
    if re.search(r'GAP\s*(US|ONLINE|\d)|OLDNAVY|OLD NAVY|BANANA\s*REPUBLIC', desc_upper):
        return ('Gap/Old Navy/BR', 'Shopping', 'Clothing')

    # Barnes & Noble variations
    if 'BARNES' in desc_upper and 'NOBLE' in desc_upper:
        return ('Barnes & Noble', 'Shopping', 'Books')

    # =========================================================================
    # DESCRIPTION CLEANUP FOR SPECIFIC MERCHANTS
    # =========================================================================

    # DoorDash has many variations
    if re.search(r'DOORDASH|DD\s*\*|DOORDASAN', desc_upper):
        return ('DoorDash', 'Food', 'Delivery')

    # Uber Eats vs Uber rideshare
    if 'UBER' in desc_upper:
        if 'EATS' in desc_upper:
            return ('Uber Eats', 'Food', 'Delivery')
        elif 'TRIP' in desc_upper or 'RIDE' in desc_upper:
            return ('Uber', 'Transport', 'Rideshare')
        # Ambiguous - check for food-related context
        if any(word in desc_upper for word in ['RESTAURANT', 'FOOD', 'DELIVERY']):
            return ('Uber Eats', 'Food', 'Delivery')

    # =========================================================================
    # CHECK HANDLING
    # =========================================================================

    # Check numbers
    if re.match(r'^CHECK\s*\d+', desc_upper):
        return ('Check', 'Cash', 'Check')

    # Check order fee
    if 'CHECK ORDER' in desc_upper:
        return ('Check Order', 'Bills', 'Fee')

    return None  # No special transformation applies


def normalize_merchant(description, rules):
    """Normalize a merchant description to (name, category, subcategory).

    Args:
        description: Raw transaction description
        rules: List of (pattern, merchant, category, subcategory) tuples

    Returns:
        Tuple of (merchant_name, category, subcategory)
    """
    # First, try special transformations
    special = apply_special_transformations(description)
    if special:
        return special

    # Clean the description for better matching
    cleaned = clean_description(description)
    desc_upper = description.upper()
    cleaned_upper = cleaned.upper()

    # Try pattern matching against both original and cleaned
    for pattern, merchant, category, subcategory in rules:
        try:
            if re.search(pattern, desc_upper, re.IGNORECASE):
                return (merchant, category, subcategory)
            if re.search(pattern, cleaned_upper, re.IGNORECASE):
                return (merchant, category, subcategory)
        except re.error:
            # Invalid regex pattern, skip
            continue

    # Fallback: extract merchant name from description, categorize as Unknown
    merchant_name = extract_merchant_name(description)
    return (merchant_name, 'Unknown', 'Unknown')
