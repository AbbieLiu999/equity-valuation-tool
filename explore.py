from sec_data import get_cik, get_company_facts

facts = get_company_facts(get_cik("NVDA"))
for tag in facts["facts"]["us-gaap"]:
    if "Payment" in tag and ("Property" in tag or "Productive" in tag):
        print(tag)

for tag in ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]:
    entries = facts["facts"]["us-gaap"][tag]["units"]["USD"]
    ends = sorted({e["end"] for e in entries if e["form"] == "10-K"})
    print(tag, ends[0], "to", ends[-1])

