from sec_data import get_cik, get_company_facts

facts = get_company_facts(get_cik("NVDA"))
entries = facts["facts"]["us-gaap"]["DebtSecuritiesCurrent"]["units"]["USD"]
for e in entries:
    print(e["end"], e["form"], e["filed"], round(e["val"] / 1e9, 1))

