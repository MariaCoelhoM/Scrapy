import scrapy
from scrapy.crawler import CrawlerProcess
import json
import re

class PokedexSpider(scrapy.Spider):
    name = "pokedex"
    start_urls = ["https://pokemondb.net/pokedex/all"]
    
    def __init__(self):
        self.pokedex_map = {}
        self.all_pokemons = []
        self.pending_abilities = {}
        self.processed = set()

    def parse(self, response):
        # Mapa nome -> número
        rows = response.css("table#pokedex tr")[1:]
        for row in rows:
            number = "".join(row.css("td:nth-child(1) ::text").getall()).strip().replace("#", "")
            name = row.css("td:nth-child(2) a::text").get()
            if name: self.pokedex_map[name] = number

        # Processa cada Pokémon
        for row in rows:
            number = "".join(row.css("td:nth-child(1) ::text").getall()).strip().replace("#", "")
            name = row.css("td:nth-child(2) a::text").get()
            url = row.css("td:nth-child(2) a::attr(href)").get()
            types = row.css("td:nth-child(3) a::text").getall()

            if name and url:
                pokemon = {
                    "number": number, "name": name, "url": f"https://pokemondb.net{url}",
                    "types": types, "height_cm": None, "weight_kg": None,
                    "evolutions": [], "abilities": [],
                    "type_effectiveness": {"weaknesses": [], "resistances": [], "immunities": [], 
                                         "super_effective_against": [], "not_very_effective_against": [], "no_effect_against": []}
                }
                yield response.follow(url, callback=self.parse_details, meta={'pokemon': pokemon})

    def parse_details(self, response):
        pokemon = response.meta['pokemon']
        key = f"{pokemon['number']}_{pokemon['name']}"

        # Altura e peso
        for row in response.css(".vitals-table tr"):
            label, value = row.css("th::text").get(), row.css("td::text").get()
            if label and value:
                if "Height" in label:
                    match = re.search(r'([\d.]+)\s*m', value)
                    if match: pokemon["height_cm"] = float(match.group(1)) * 100
                elif "Weight" in label:
                    match = re.search(r'([\d.]+)\s*kg', value)
                    if match: pokemon["weight_kg"] = float(match.group(1))

        # Evoluções
        for evo in response.css(".infocard-list-evo .infocard"):
            evo_name = evo.css(".ent-name::text").get()
            if evo_name and evo_name != pokemon['name']:
                evo_url = evo.css("a::attr(href)").get()
                pokemon["evolutions"].append({
                    "number": self.pokedex_map.get(evo_name),
                    "name": evo_name,
                    "url": f"https://pokemondb.net{evo_url}" if evo_url else None,
                    "level": evo.css(".infocard-lg-data small::text").re_first(r'Level (\d+)'),
                    "item": evo.css(".infocard-lg-data small a::text").get()
                })

        # Efetividades de tipo
        self.calculate_type_effectiveness(pokemon)

        # Habilidades
        ability_links = []
        for selector in ['th:contains("Abilities") + td a', 'th:contains("Ability") + td a', 'a[href*="/ability/"].ent-name']:
            ability_links = response.css(selector)
            if ability_links: break
        
        if ability_links:
            self.pending_abilities[key] = {
                'pokemon': pokemon, 'expected': len(ability_links), 'collected': 0, 'abilities': []
            }
            for link in ability_links:
                name, url = link.css("::text").get(), link.css("::attr(href)").get()
                if name and url:
                    yield response.follow(url, callback=self.parse_ability, 
                                        meta={'key': key, 'name': name.strip(), 'url': response.urljoin(url)})
        else:
            self.save_pokemon(pokemon, key)

    def parse_ability(self, response):
        key, name, url = response.meta['key'], response.meta['name'], response.meta['url']
        
        if key not in self.pending_abilities: return

        # Busca descrição
        description = "Descrição não disponível"
        for selector in ['main .grid-col p::text', '.grid-col p::text', 'main p::text', 'p::text']:
            descriptions = response.css(selector).getall()
            for desc in descriptions:
                cleaned = desc.strip()
                if len(cleaned) > 15 and any(c.isalpha() for c in cleaned):
                    description = cleaned
                    break
            if description != "Descrição não disponível": break

        self.pending_abilities[key]['abilities'].append({"name": name, "url": url, "description": description})
        self.pending_abilities[key]['collected'] += 1

        # Se coletou todas, salva
        pending = self.pending_abilities[key]
        if pending['collected'] >= pending['expected']:
            pending['pokemon']['abilities'] = pending['abilities']
            self.save_pokemon(pending['pokemon'], key)
            del self.pending_abilities[key]

    def save_pokemon(self, pokemon, key):
        if key not in self.processed:
            self.all_pokemons.append(pokemon)
            self.processed.add(key)

    def calculate_type_effectiveness(self, pokemon):
        # Tabela de efetividades defensivas
        chart = {
            'Normal': {'weak': ['Fighting'], 'resist': [], 'immune': ['Ghost']},
            'Fire': {'weak': ['Ground', 'Rock', 'Water'], 'resist': ['Bug', 'Steel', 'Fire', 'Grass', 'Ice', 'Fairy'], 'immune': []},
            'Water': {'weak': ['Grass', 'Electric'], 'resist': ['Steel', 'Fire', 'Water', 'Ice'], 'immune': []},
            'Electric': {'weak': ['Ground'], 'resist': ['Flying', 'Steel', 'Electric'], 'immune': []},
            'Grass': {'weak': ['Flying', 'Poison', 'Bug', 'Fire', 'Ice'], 'resist': ['Ground', 'Water', 'Grass', 'Electric'], 'immune': []},
            'Ice': {'weak': ['Fighting', 'Rock', 'Steel', 'Fire'], 'resist': ['Ice'], 'immune': []},
            'Fighting': {'weak': ['Flying', 'Psychic', 'Fairy'], 'resist': ['Rock', 'Bug', 'Dark'], 'immune': []},
            'Poison': {'weak': ['Ground', 'Psychic'], 'resist': ['Fighting', 'Poison', 'Bug', 'Grass', 'Fairy'], 'immune': []},
            'Ground': {'weak': ['Water', 'Grass', 'Ice'], 'resist': ['Poison', 'Rock'], 'immune': ['Electric']},
            'Flying': {'weak': ['Rock', 'Electric', 'Ice'], 'resist': ['Fighting', 'Bug', 'Grass'], 'immune': ['Ground']},
            'Psychic': {'weak': ['Bug', 'Ghost', 'Dark'], 'resist': ['Fighting', 'Psychic'], 'immune': []},
            'Bug': {'weak': ['Flying', 'Rock', 'Fire'], 'resist': ['Fighting', 'Ground', 'Grass'], 'immune': []},
            'Rock': {'weak': ['Fighting', 'Ground', 'Steel', 'Water', 'Grass'], 'resist': ['Normal', 'Flying', 'Poison', 'Fire'], 'immune': []},
            'Ghost': {'weak': ['Ghost', 'Dark'], 'resist': ['Poison', 'Bug'], 'immune': ['Normal', 'Fighting']},
            'Dragon': {'weak': ['Ice', 'Dragon', 'Fairy'], 'resist': ['Fire', 'Water', 'Electric', 'Grass'], 'immune': []},
            'Dark': {'weak': ['Fighting', 'Bug', 'Fairy'], 'resist': ['Ghost', 'Dark'], 'immune': ['Psychic']},
            'Steel': {'weak': ['Fighting', 'Ground', 'Fire'], 'resist': ['Normal', 'Flying', 'Rock', 'Bug', 'Steel', 'Grass', 'Psychic', 'Ice', 'Dragon', 'Fairy'], 'immune': ['Poison']},
            'Fairy': {'weak': ['Poison', 'Steel'], 'resist': ['Fighting', 'Bug', 'Dark'], 'immune': ['Dragon']}
        }
        
        # Tabela ofensiva
        offensive = {
            'Normal': {'strong': [], 'weak': ['Rock', 'Steel'], 'none': ['Ghost']},
            'Fire': {'strong': ['Grass', 'Ice', 'Bug', 'Steel'], 'weak': ['Fire', 'Water', 'Rock', 'Dragon'], 'none': []},
            'Water': {'strong': ['Fire', 'Ground', 'Rock'], 'weak': ['Water', 'Grass', 'Dragon'], 'none': []},
            'Electric': {'strong': ['Water', 'Flying'], 'weak': ['Electric', 'Grass', 'Dragon'], 'none': ['Ground']},
            'Grass': {'strong': ['Water', 'Ground', 'Rock'], 'weak': ['Fire', 'Grass', 'Poison', 'Flying', 'Bug', 'Dragon', 'Steel'], 'none': []},
            'Ice': {'strong': ['Grass', 'Ground', 'Flying', 'Dragon'], 'weak': ['Fire', 'Water', 'Ice', 'Steel'], 'none': []},
            'Fighting': {'strong': ['Normal', 'Ice', 'Rock', 'Dark', 'Steel'], 'weak': ['Poison', 'Flying', 'Psychic', 'Bug', 'Fairy'], 'none': ['Ghost']},
            'Poison': {'strong': ['Grass', 'Fairy'], 'weak': ['Poison', 'Ground', 'Rock', 'Ghost'], 'none': ['Steel']},
            'Ground': {'strong': ['Fire', 'Electric', 'Poison', 'Rock', 'Steel'], 'weak': ['Grass', 'Bug'], 'none': ['Flying']},
            'Flying': {'strong': ['Electric', 'Fighting', 'Bug', 'Grass'], 'weak': ['Electric', 'Rock', 'Steel'], 'none': []},
            'Psychic': {'strong': ['Fighting', 'Poison'], 'weak': ['Psychic', 'Steel'], 'none': ['Dark']},
            'Bug': {'strong': ['Grass', 'Psychic', 'Dark'], 'weak': ['Fire', 'Fighting', 'Poison', 'Flying', 'Ghost', 'Steel', 'Fairy'], 'none': []},
            'Rock': {'strong': ['Fire', 'Ice', 'Flying', 'Bug'], 'weak': ['Fighting', 'Ground', 'Steel'], 'none': []},
            'Ghost': {'strong': ['Psychic', 'Ghost'], 'weak': ['Dark'], 'none': ['Normal']},
            'Dragon': {'strong': ['Dragon'], 'weak': ['Steel'], 'none': ['Fairy']},
            'Dark': {'strong': ['Psychic', 'Ghost'], 'weak': ['Fighting', 'Dark', 'Fairy'], 'none': []},
            'Steel': {'strong': ['Ice', 'Rock', 'Fairy'], 'weak': ['Fire', 'Water', 'Electric', 'Steel'], 'none': []},
            'Fairy': {'strong': ['Fighting', 'Dragon', 'Dark'], 'weak': ['Fire', 'Poison', 'Steel'], 'none': []}
        }

        # Calcula defesas
        weak, resist, immune = set(), set(), set()
        for ptype in pokemon['types']:
            if ptype in chart:
                weak.update(chart[ptype]['weak'])
                resist.update(chart[ptype]['resist'])
                immune.update(chart[ptype]['immune'])

        # Remove conflitos
        resist -= immune
        weak -= immune | resist

        # Calcula ataques
        strong, not_very, no_effect = set(), set(), set()
        for ptype in pokemon['types']:
            if ptype in offensive:
                strong.update(offensive[ptype]['strong'])
                not_very.update(offensive[ptype]['weak'])
                no_effect.update(offensive[ptype]['none'])

        pokemon["type_effectiveness"]["weaknesses"] = list(weak)
        pokemon["type_effectiveness"]["resistances"] = list(resist)
        pokemon["type_effectiveness"]["immunities"] = list(immune)
        pokemon["type_effectiveness"]["super_effective_against"] = list(strong)
        pokemon["type_effectiveness"]["not_very_effective_against"] = list(not_very)
        pokemon["type_effectiveness"]["no_effect_against"] = list(no_effect)

    def closed(self, reason):
        # Salva pendências
        for key, pending in self.pending_abilities.items():
            if key not in self.processed:
                pending['pokemon']['abilities'] = pending['abilities']
                self.all_pokemons.append(pending['pokemon'])

        # Remove duplicatas finais
        unique = {}
        for p in self.all_pokemons:
            if p['number'] not in unique:
                unique[p['number']] = p

        # Ordena e salva
        final_list = sorted(unique.values(), key=lambda x: int(x['number']) if x['number'].isdigit() else 9999)
        
        with open("pokemons_final.json", "w", encoding="utf-8") as f:
            json.dump(final_list, f, ensure_ascii=False, indent=2)
        
        print(f"✅ CONCLUÍDO! {len(final_list)} Pokémons salvos (sem duplicatas)")

if __name__ == "__main__":
    process = CrawlerProcess({
        "LOG_LEVEL": "WARNING",
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 8,
        "AUTOTHROTTLE_ENABLED": True,
        "RETRY_TIMES": 3
    })
    process.crawl(PokedexSpider)
    process.start()