import requests
from bs4 import BeautifulSoup

# unused, uses RadioBrowser API instead

def search_stations(query: str):
    request = requests.get(f"https://dir.xiph.org/search?q={query}")
    soup = BeautifulSoup(request.text, "html.parser")
    results = soup.find_all("div", class_="card shadow-sm mt-3")

    parsed_results = []
    for result in results:
        genres = []
        for genre in result.find_all("a", class_="badge badge-secondary"):
            genres.append(genre.get_text(strip=True))
        parsed_results.append({
            "station": result.find("h5", class_="card-title").get_text(strip=True),
            "show": result.find("h6", class_="card-subtitle mb-2 text-muted").get_text(strip=True),
            "genres": genres,
            "url": result.find("a", class_="btn btn-sm btn-primary")["href"],
            "stream_type": result.find("a", class_="badge badge-primary").get_text(strip=True)
        })

results = search_stations("- 0 N -")