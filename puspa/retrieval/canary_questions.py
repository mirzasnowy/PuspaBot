"""Frozen 30-question canary set (E-14, plan section 7.4).

Each question shares rare tokens (product names, codes, numbers)
with exactly one expected content, so a working embedder ranks
that content at the top and a broken one does not. The set is data,
not tuning material: never edit a question to make a measurement
look better; record a new baseline instead (see
``record_canary_baseline`` in ``embedding_health``).
"""

from __future__ import annotations

CANARY_QUESTIONS: tuple[dict[str, str], ...] = (
    {
        "question": "What is the cheapest price for the Asus Vivobook 14 laptop?",
        "content": "Asus Vivobook 14 laptop cheapest price Rp8.499.000 at Tokopedia seller LapakJaya",
    },
    {
        "question": "How much does the Samsung Galaxy A55 cost?",
        "content": "Samsung Galaxy A55 price Rp5.999.000 at Shopee seller GadgetKita",
    },
    {
        "question": "How do I check my PLN electricity bill for this month?",
        "content": "PLN electricity bill check: enter customer number 123456789012 on the PLN Mobile billing page",
    },
    {
        "question": "Where can I pay my PDAM water bill online?",
        "content": "PDAM water bill payment online via Tokopedia billing menu with customer number 987654321",
    },
    {
        "question": "What is the price of an iPhone 13 128GB?",
        "content": "iPhone 13 128GB price Rp9.750.000 at Blibli seller AppleResmi",
    },
    {
        "question": "Is the Xiaomi Redmi Note 13 Pro cheaper on Shopee or Tokopedia?",
        "content": "Xiaomi Redmi Note 13 Pro cheaper on Shopee Rp3.899.000 versus Tokopedia Rp4.099.000",
    },
    {
        "question": "How do I buy a Telkomsel 10GB data package?",
        "content": "Telkomsel 10GB data package Rp55.000 buy with pulsa code *363# or MyTelkomsel app",
    },
    {
        "question": "What is the monthly cost of IndiHome 50Mbps internet?",
        "content": "IndiHome 50Mbps internet monthly cost Rp355.000 including tax",
    },
    {
        "question": "Which store sells the Logitech MX Master 3S mouse cheapest?",
        "content": "Logitech MX Master 3S mouse cheapest at Tokopedia Rp1.899.000 seller TeknoStore",
    },
    {
        "question": "What is the price of the Epson L3210 printer?",
        "content": "Epson L3210 printer price Rp2.450.000 at Shopee seller PrintZone",
    },
    {
        "question": "Where can I buy Royal Canin cat food 2kg?",
        "content": "Royal Canin cat food 2kg Rp385.000 at Tokopedia seller PetLovers",
    },
    {
        "question": "How much is a Uniqlo AIRism t-shirt?",
        "content": "Uniqlo AIRism t-shirt price Rp149.000 at Uniqlo official store",
    },
    {
        "question": "How do I top up GoPay with BCA mobile banking?",
        "content": "GoPay top up via BCA mobile banking m-BCA menu GoPay customer number 081234567890",
    },
    {
        "question": "When is the BPJS Kesehatan monthly contribution due?",
        "content": "BPJS Kesehatan monthly contribution due on the 10th of every month",
    },
    {
        "question": "What is the price of Sony WH-1000XM5 headphones?",
        "content": "Sony WH-1000XM5 headphones price Rp4.999.000 at Blibli seller SonyOfficial",
    },
    {
        "question": "Is the Nintendo Switch OLED in stock anywhere?",
        "content": "Nintendo Switch OLED in stock at Shopee seller GameZone price Rp5.299.000",
    },
    {
        "question": "How much does a Daikin 1PK air conditioner cost including installation?",
        "content": "Daikin 1PK air conditioner Rp4.200.000 including installation at Tokopedia seller CoolAir",
    },
    {
        "question": "How do I buy Indosat 5GB pulsa data?",
        "content": "Indosat 5GB pulsa data Rp35.000 dial *123# or myIM3 app",
    },
    {
        "question": "What is the price of a Philips LED bulb 12 watt pack?",
        "content": "Philips LED bulb 12 watt pack of 4 price Rp89.000 at Shopee seller LampuTerang",
    },
    {
        "question": "Which shade of Wardah lipstick is best for warm skin?",
        "content": "Wardah lipstick shade 03 Peach Pink suits warm skin tone Rp42.000",
    },
    {
        "question": "What is the cash price of a Yamaha NMAX 155 motorcycle?",
        "content": "Yamaha NMAX 155 motorcycle cash price Rp31.615.000 on the road Jakarta",
    },
    {
        "question": "How much is the IKEA Markus office chair?",
        "content": "IKEA Markus office chair price Rp2.999.000 at IKEA Alam Sutera",
    },
    {
        "question": "Where can I buy the book Atomic Habits in Indonesian?",
        "content": "Atomic Habits Indonesian edition Rp99.000 at Gramedia bookstore",
    },
    {
        "question": "How do I check my remaining XL data quota?",
        "content": "XL remaining data quota check dial *123# then menu myXL app",
    },
    {
        "question": "What is the price of a Sharp 43 inch Android TV?",
        "content": "Sharp 43 inch Android TV price Rp3.799.000 at Blibli seller SharpOfficial",
    },
    {
        "question": "How do I transfer OVO balance to a bank account?",
        "content": "OVO balance transfer to bank account fee Rp2.500 via OVO app transfer menu",
    },
    {
        "question": "Which Anker charger supports 65W fast charging?",
        "content": "Anker 65W fast charger Nano II price Rp549.000 supports USB-C PD",
    },
    {
        "question": "Is there a Tokopedia promo code for electronics this week?",
        "content": "Tokopedia electronics promo code ELEKTRO10 gives 10 percent off this week",
    },
    {
        "question": "What time does the first KRL train leave Bogor station?",
        "content": "First KRL train leaves Bogor station at 04:00 to Jakarta Kota",
    },
    {
        "question": "How much is the TransJakarta bus fare?",
        "content": "TransJakarta bus fare Rp3.500 per trip with JakLingko card",
    },
)


def canary_corpus() -> list[str]:
    """The 30 expected canary contents, in question order."""
    return [item["content"] for item in CANARY_QUESTIONS]


__all__ = ["CANARY_QUESTIONS", "canary_corpus"]
