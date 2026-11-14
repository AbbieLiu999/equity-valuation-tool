import requests

headers = {"User-Agent": "Dan Liu abbieliu666@gmail.com"}
url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"  # Apple
data = requests.get(url, headers=headers).json()

revenue = data["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
for item in revenue[-5:]:
    print(item["end"], item["form"], item["val"])