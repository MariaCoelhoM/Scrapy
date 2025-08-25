import scrapy
import json


class PokedexSpider(scrapy.Spider):
    name = "pokedex"
    start_urls = ["https://pokemondb.net/pokedex/all"]

    def parse(self, response):
        pokemons = []
        rows = response.css("table#pokedex tr")[1:]  # Ignora header
        for row in rows:
            number_raw = row.css("td:nth-child(1) ::text").getall()
            number = "".join(number_raw).strip().replace("#", "")

            name = row.css("td:nth-child(2) a::text").get()
            url = row.css("td:nth-child(2) a::attr(href)").get()
            types = row.css("td:nth-child(3) a::text").getall()

            pokemons.append({
                "number": number,
                "name": name,
                "url": "https://pokemondb.net" + url,
                "types": types
            })

        # salva JSON no final
        with open("pokemons.json", "w", encoding="utf-8") as f:
            json.dump(pokemons, f, ensure_ascii=False, indent=2)

        self.log(f"{len(pokemons)} pokemons salvos!")
