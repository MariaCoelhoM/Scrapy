import scrapy
import json
from scrapy.crawler import CrawlerProcess

class AbilitiesSpider(scrapy.Spider):
    name = "abilities"

    def start_requests(self):
        # Lê os pokemons do JSON base (gerado pelo spider principal)
        with open("pokemons.json", "r", encoding="utf-8") as f:
            pokemons = json.load(f)
        for pokemon in pokemons:
            yield scrapy.Request(
                url=pokemon["url"],
                callback=self.parse_pokemon,
                meta={"pokemon": pokemon}
            )

    def parse_pokemon(self, response):
        pokemon = response.meta["pokemon"]

        abilities = []
        # Seleciona a tabela de habilidades (primeira que aparece)
        rows = response.css("table.vitals-table tr")

        for row in rows:
            header = row.css("th::text").get()
            if header and "Ability" in header:
                for ability in row.css("td a.ent-name"):
                    ability_name = ability.css("::text").get()
                    ability_url = response.urljoin(ability.attrib["href"])

                    abilities.append({
                        "name": ability_name,
                        "url": ability_url,
                        "description": None  # Preenchemos depois
                    })

        # Agora vamos visitar cada link de habilidade para pegar a descrição
        if abilities:
            for ability in abilities:
                yield scrapy.Request(
                    url=ability["url"],
                    callback=self.parse_ability,
                    meta={"pokemon": pokemon, "abilities": abilities, "current": ability}
                )
        else:
            pokemon["abilities"] = []
            yield pokemon

    def parse_ability(self, response):
        pokemon = response.meta["pokemon"]
        abilities = response.meta["abilities"]
        current = response.meta["current"]

        # Captura a descrição (tentando mais de um seletor por segurança)
        description = response.css("main p::text").get()
        if not description:
            description = response.css("div.grid-row p::text").get()
        if not description:
            description = "Descrição não encontrada"

        current["description"] = description.strip()

        # Verifica se todas já foram preenchidas
        if all("description" in ab and ab["description"] for ab in abilities):
            pokemon["abilities"] = abilities
            yield pokemon


# Para rodar direto pelo terminal
if __name__ == "__main__":
    process = CrawlerProcess(settings={
        "FEEDS": {
            "pokemons_abilities.json": {"format": "json", "encoding": "utf-8"},
        },
        "LOG_LEVEL": "INFO",
    })
    process.crawl(AbilitiesSpider)
    process.start()
