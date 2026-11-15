
import requests

token = "eyJ0eXAiOiJKV1QiLCJvcmlnaW4iOiJFYXJ0aGRhdGEgTG9naW4iLCJzaWciOiJlZGxqd3RwdWJrZXlfb3BzIiwiYWxnIjoiUlMyNTYifQ.eyJ0eXBlIjoiVXNlciIsInVpZCI6ImpvZS5raXdhbiIsInN1YiI6ImpvZS5raXdhbiIsImV4cCI6MTc5OTgzOTY3OSwiaWF0IjoxNzk0NjU1Njc5LCJpc3MiOiJodHRwczovL3Vycy5lYXJ0aGRhdGEubmFzYS5nb3YiLCJpZGVudGl0eV9wcm92aWRlciI6ImVkbF9vcHMiLCJhY3IiOiJlZGwiLCJhc3N1cmFuY2VfbGV2ZWwiOjN9.Q_D4Uul1URuavgYT6LLD0EH70l4oWYrUv--Jb2UdnXwpL84wFq_K-9LfdvjEt4RYZ6uEdASA4MvqN4XqU05pwTkO9MjiBMH2LR9_Y4RrgBbwQhGpyov5cwRha2WsTz2ZSVoSsaqTRvWprBv8gqfuLQuKF4rWw5ODnnr-zTo8iF7pSzKCn_QFTvuxnl2a-Glc3VaY10gx9HYB1ixYJEr3azjz5d18R2CQwUy0IjbiSc-nfWXa9dOORQhZ04Net5TSmWaxf-6vPjTg-78oZPm3HCIhPv6mAApSva5RdAaYBMYyEuZ1NZJjoohsS25-OxH-xeHTcP47yyiJQUoRyTEeUQ"

url = "https://nrt3.modaps.eosdis.nasa.gov/archive/FIRMS/modis-c6.1/Global/"

headers = {
    "Authorization": f"Bearer {token}",
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers)

print("HTTP status:", response.status_code)
print("Requested URL:", url)
print("Final URL:", response.url)
print("Content type:", response.headers.get("Content-Type"))

if response.status_code == 200:
    print("\nConnection successful.")
    print("Response preview:")
    print(response.text[:500])

    if "MODIS_C6_1_Global_MCD14DL_NRT_" in response.text:
        print("\nMODIS filenames detected in response.")
    else:
        print("\nNo MODIS filenames detected. We need to investigate the response.")
else:
    print("\nRequest failed.")
