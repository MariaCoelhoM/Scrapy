import scrapy
from scrapy.crawler import CrawlerProcess
import json
import re
from scrapy.utils.project import get_project_settings

class PokedexFinalSpider(scrapy.Spider):
    name = "pokedex_final"
    start_urls = ["https://pokemondb.net/pokedex/all"]
    
    def __init__(self):
        self.pokedex_map = {}
        self.all_pokemons_data = []
        self.pending_abilities = {}  # Para rastrear habilidades pendentes
        self.processed_pokemons = set()  # Para evitar duplicatas

    def parse(self, response):
        # Cria mapa nome -> número para evoluções
        rows = response.css("table#pokedex tr")[1:]
        for row in rows:
            number_raw = row.css("td:nth-child(1) ::text").getall()
            number = "".join(number_raw).strip().replace("#", "")
            name = row.css("td:nth-child(2) a::text").get()
            if name:
                self.pokedex_map[name] = number

        # Coleta dados básicos e vai para página de cada Pokémon
        for row in rows:
            number_raw = row.css("td:nth-child(1) ::text").getall()
            number = "".join(number_raw).strip().replace("#", "")
            name = row.css("td:nth-child(2) a::text").get()
            url = row.css("td:nth-child(2) a::attr(href)").get()
            types = row.css("td:nth-child(3) a::text").getall()

            if not name or not url:
                continue

            pokemon_data = {
                "number": number,
                "name": name,
                "url": "https://pokemondb.net" + url,
                "types": types,
                "height_cm": None,
                "weight_kg": None,
                "evolutions": [],
                "abilities": [],
                "type_effectiveness": {
                    "weaknesses": [],  # 2x damage
                    "resistances": [],  # 0.5x damage
                    "immunities": [],   # 0x damage
                    "super_effective_against": [],  # This Pokemon deals 2x damage to
                    "not_very_effective_against": [], # This Pokemon deals 0.5x damage to
                    "no_effect_against": []  # This Pokemon deals 0x damage to
                }
            }

            yield response.follow(url, callback=self.parse_details, meta={'pokemon_data': pokemon_data})

    def parse_details(self, response):
        pokemon_data = response.meta['pokemon_data']
        pokemon_key = f"{pokemon_data['number']}_{pokemon_data['name']}"

        # ===== Altura e peso =====
        vitals = response.css(".vitals-table tr")
        for row in vitals:
            label = row.css("th::text").get()
            value = row.css("td::text").get()
            if label and value:
                if "Height" in label:
                    match = re.search(r'([\d.]+)\s*m', value)
                    if match:
                        pokemon_data["height_cm"] = float(match.group(1)) * 100
                elif "Weight" in label:
                    match = re.search(r'([\d.]+)\s*kg', value)
                    if match:
                        pokemon_data["weight_kg"] = float(match.group(1))

        # ===== Evoluções =====
        evolution_blocks = response.css(".infocard-list-evo .infocard")
        evolutions = []
        for evo in evolution_blocks:
            evo_name = evo.css(".ent-name::text").get()
            if not evo_name or evo_name == pokemon_data['name']:
                continue

            evo_url = evo.css("a::attr(href)").get()
            evo_level = evo.css(".infocard-lg-data small::text").re_first(r'Level (\d+)')
            evo_item = evo.css(".infocard-lg-data small a::text").get()
            evo_number = self.pokedex_map.get(evo_name)

            evolutions.append({
                "number": evo_number,
                "name": evo_name,
                "url": "https://pokemondb.net" + evo_url if evo_url else None,
                "level": evo_level,
                "item": evo_item
            })

        pokemon_data["evolutions"] = evolutions

        # ===== Efetividade de Tipos =====
        self.parse_type_effectiveness(response, pokemon_data)

        # ===== Habilidades - Sistema Simplificado =====
        # Busca por diferentes seletores possíveis para abilities
        ability_selectors = [
            'th:contains("Abilities") + td a.ent-name',
            'th:contains("Ability") + td a.ent-name',
            '.vitals-table th:contains("Abilities") + td a',
            '.vitals-table th:contains("Ability") + td a',
            'a[href*="/ability/"].ent-name'
        ]
        
        ability_links = []
        for selector in ability_selectors:
            ability_links = response.css(selector)
            if ability_links:
                break
        
        if ability_links:
            # Inicializa o controle de habilidades pendentes para este Pokémon
            self.pending_abilities[pokemon_key] = {
                'pokemon_data': pokemon_data,
                'expected_count': len(ability_links),
                'collected_count': 0,
                'abilities': []
            }
            
            for ability_link in ability_links:
                ability_name = ability_link.css("::text").get()
                ability_url = ability_link.css("::attr(href)").get()
                
                if ability_name and ability_url:
                    full_url = response.urljoin(ability_url)
                    yield response.follow(
                        full_url,
                        callback=self.parse_ability,
                        meta={
                            'pokemon_key': pokemon_key,
                            'ability_name': ability_name.strip(),
                            'ability_url': full_url
                        },
                        dont_filter=True  # Permite requisições duplicadas se necessário
                    )
        else:
            # Se não encontrou habilidades, salva o Pokémon imediatamente
            self.logger.info(f"Nenhuma habilidade encontrada para {pokemon_data['name']} - salvando diretamente")
            self.all_pokemons_data.append(pokemon_data)

    def parse_ability(self, response):
        pokemon_key = response.meta['pokemon_key']
        ability_name = response.meta['ability_name']
        ability_url = response.meta['ability_url']

        if pokemon_key not in self.pending_abilities:
            self.logger.error(f"Pokemon key {pokemon_key} não encontrado em pending_abilities")
            return

        # Tenta diferentes seletores para encontrar a descrição
        description = None
        
        # Seletores possíveis para descrição
        description_selectors = [
            'main .grid-col p::text',
            '.grid-col p::text', 
            'main p::text',
            '.mw-content p::text',
            '.sv-tabs-panel p::text',
            'p::text'
        ]
        
        for selector in description_selectors:
            descriptions = response.css(selector).getall()
            if descriptions:
                # Pega a primeira descrição não vazia e significativa
                for desc in descriptions:
                    cleaned_desc = desc.strip()
                    # Evita textos muito curtos ou que são apenas números/símbolos
                    if cleaned_desc and len(cleaned_desc) > 15 and any(c.isalpha() for c in cleaned_desc):
                        description = cleaned_desc
                        break
                if description:
                    break
        
        if not description:
            description = "Descrição não disponível"
            self.logger.warning(f"Descrição não encontrada para habilidade: {ability_name}")

        # Adiciona a habilidade à lista do Pokémon
        ability_data = {
            "name": ability_name,
            "url": ability_url,
            "description": description
        }
        
        self.pending_abilities[pokemon_key]['abilities'].append(ability_data)
        self.pending_abilities[pokemon_key]['collected_count'] += 1

        # Verifica se coletou todas as habilidades
        pending_info = self.pending_abilities[pokemon_key]
        if pending_info['collected_count'] >= pending_info['expected_count']:
            # Adiciona as habilidades ao pokemon_data e salva
            pokemon_data = pending_info['pokemon_data']
            pokemon_data['abilities'] = pending_info['abilities']
            
            # Verifica se já não foi processado para evitar duplicatas
            if pokemon_key not in self.processed_pokemons:
                self.all_pokemons_data.append(pokemon_data)
                self.processed_pokemons.add(pokemon_key)
                self.logger.info(f"Pokemon {pokemon_data['name']} (#{pokemon_data['number']}) salvo com {len(pokemon_data['abilities'])} habilidades")
            else:
                self.logger.warning(f"Pokemon {pokemon_data['name']} (#{pokemon_data['number']}) já foi processado - ignorando duplicata")
            
            # Remove do controle de pendências
            del self.pending_abilities[pokemon_key]

    def parse_type_effectiveness(self, response, pokemon_data):
        """Extrai informações de efetividade de tipos da página do Pokémon"""
        
        # Debug: Log para verificar se estamos na página correta
        self.logger.info(f"Processando efetividades para {pokemon_data['name']}")
        
        # Múltiplos seletores para encontrar a tabela de defesas de tipo
        type_chart_selectors = [
            'h3:contains("Type defenses") + table tr',
            'h3:contains("Type effectiveness") + table tr', 
            '.sv-tabs-panel table tr',
            'table.data-table tr',
            'table tr td.type-fx-cell',
            '.type-chart tr'
        ]
        
        found_data = False
        
        # Tenta diferentes abordagens para encontrar as efetividades
        for selector in type_chart_selectors:
            rows = response.css(selector)
            if rows:
                self.logger.info(f"Encontrou {len(rows)} linhas com selector: {selector}")
                for row in rows:
                    # Procura por células que contenham multiplicadores
                    cells = row.css('td')
                    for cell in cells:
                        # Pega todo o texto da célula
                        cell_text = ''.join(cell.css('::text').getall()).strip()
                        type_links = cell.css('a[href*="/type/"]::text').getall()
                        
                        if cell_text and type_links:
                            for type_name in type_links:
                                self.classify_type_effectiveness(cell_text, type_name.strip(), pokemon_data)
                                found_data = True
                
                if found_data:
                    break
        
        # Se não encontrou dados específicos, tenta uma abordagem mais ampla
        if not found_data:
            self.logger.info(f"Tentando abordagem alternativa para {pokemon_data['name']}")
            
            # Busca por qualquer texto que contenha multiplicadores perto de tipos
            all_elements = response.css('*:contains("×"), *:contains("2"), *:contains("½"), *:contains("0")')
            
            for elem in all_elements:
                elem_text = ''.join(elem.css('::text').getall()).strip()
                # Procura por tipos próximos
                nearby_types = elem.css('a[href*="/type/"]::text').getall()
                if not nearby_types:
                    # Busca em elementos pais/filhos próximos
                    parent = elem.css('..').get()
                    if parent:
                        nearby_types = scrapy.Selector(text=parent).css('a[href*="/type/"]::text').getall()
                
                for type_name in nearby_types:
                    self.classify_type_effectiveness(elem_text, type_name.strip(), pokemon_data)
                    found_data = True
        
        # Se ainda não encontrou, usa cálculo baseado apenas nos tipos
        if not found_data:
            self.logger.warning(f"Não encontrou dados de efetividade na página para {pokemon_data['name']}, calculando baseado nos tipos")
            self.calculate_defensive_effectiveness_from_types(pokemon_data)
        
        # Calcula efetividade ofensiva baseada nos tipos do Pokémon
        self.calculate_offensive_effectiveness(pokemon_data)
    
    def classify_type_effectiveness(self, text, type_name, pokemon_data):
        """Classifica a efetividade baseada no texto encontrado"""
        text_lower = text.lower()
        
        # Padrões para identificar multiplicadores
        if any(pattern in text for pattern in ['2×', '×2', '2.0', ' 2 ']):
            if type_name not in pokemon_data["type_effectiveness"]["weaknesses"]:
                pokemon_data["type_effectiveness"]["weaknesses"].append(type_name)
                self.logger.info(f"{pokemon_data['name']} é fraco contra {type_name}")
        
        elif any(pattern in text for pattern in ['½×', '×½', '0.5', '1/2']):
            if type_name not in pokemon_data["type_effectiveness"]["resistances"]:
                pokemon_data["type_effectiveness"]["resistances"].append(type_name)
                self.logger.info(f"{pokemon_data['name']} resiste a {type_name}")
        
        elif any(pattern in text for pattern in ['0×', '×0', '0.0', ' 0 ']):
            if type_name not in pokemon_data["type_effectiveness"]["immunities"]:
                pokemon_data["type_effectiveness"]["immunities"].append(type_name)
                self.logger.info(f"{pokemon_data['name']} é imune a {type_name}")
    
    def calculate_defensive_effectiveness_from_types(self, pokemon_data):
        """Calcula efetividades defensivas baseado apenas nos tipos do Pokémon"""
        
        # Tabela completa de efetividades defensivas
        defensive_chart = {
            'Normal': {
                'weak_to': ['Fighting'],
                'resist': [],
                'immune_to': ['Ghost']
            },
            'Fire': {
                'weak_to': ['Ground', 'Rock', 'Water'],
                'resist': ['Bug', 'Steel', 'Fire', 'Grass', 'Ice', 'Fairy'],
                'immune_to': []
            },
            'Water': {
                'weak_to': ['Grass', 'Electric'],
                'resist': ['Steel', 'Fire', 'Water', 'Ice'],
                'immune_to': []
            },
            'Electric': {
                'weak_to': ['Ground'],
                'resist': ['Flying', 'Steel', 'Electric'],
                'immune_to': []
            },
            'Grass': {
                'weak_to': ['Flying', 'Poison', 'Bug', 'Fire', 'Ice'],
                'resist': ['Ground', 'Water', 'Grass', 'Electric'],
                'immune_to': []
            },
            'Ice': {
                'weak_to': ['Fighting', 'Rock', 'Steel', 'Fire'],
                'resist': ['Ice'],
                'immune_to': []
            },
            'Fighting': {
                'weak_to': ['Flying', 'Psychic', 'Fairy'],
                'resist': ['Rock', 'Bug', 'Dark'],
                'immune_to': []
            },
            'Poison': {
                'weak_to': ['Ground', 'Psychic'],
                'resist': ['Fighting', 'Poison', 'Bug', 'Grass', 'Fairy'],
                'immune_to': []
            },
            'Ground': {
                'weak_to': ['Water', 'Grass', 'Ice'],
                'resist': ['Poison', 'Rock'],
                'immune_to': ['Electric']
            },
            'Flying': {
                'weak_to': ['Rock', 'Electric', 'Ice'],
                'resist': ['Fighting', 'Bug', 'Grass'],
                'immune_to': ['Ground']
            },
            'Psychic': {
                'weak_to': ['Bug', 'Ghost', 'Dark'],
                'resist': ['Fighting', 'Psychic'],
                'immune_to': []
            },
            'Bug': {
                'weak_to': ['Flying', 'Rock', 'Fire'],
                'resist': ['Fighting', 'Ground', 'Grass'],
                'immune_to': []
            },
            'Rock': {
                'weak_to': ['Fighting', 'Ground', 'Steel', 'Water', 'Grass'],
                'resist': ['Normal', 'Flying', 'Poison', 'Fire'],
                'immune_to': []
            },
            'Ghost': {
                'weak_to': ['Ghost', 'Dark'],
                'resist': ['Poison', 'Bug'],
                'immune_to': ['Normal', 'Fighting']
            },
            'Dragon': {
                'weak_to': ['Ice', 'Dragon', 'Fairy'],
                'resist': ['Fire', 'Water', 'Electric', 'Grass'],
                'immune_to': []
            },
            'Dark': {
                'weak_to': ['Fighting', 'Bug', 'Fairy'],
                'resist': ['Ghost', 'Dark'],
                'immune_to': ['Psychic']
            },
            'Steel': {
                'weak_to': ['Fighting', 'Ground', 'Fire'],
                'resist': ['Normal', 'Flying', 'Rock', 'Bug', 'Steel', 'Grass', 'Psychic', 'Ice', 'Dragon', 'Fairy'],
                'immune_to': ['Poison']
            },
            'Fairy': {
                'weak_to': ['Poison', 'Steel'],
                'resist': ['Fighting', 'Bug', 'Dark'],
                'immune_to': ['Dragon']
            }
        }
        
        all_weaknesses = set()
        all_resistances = set()
        all_immunities = set()
        
        # Para cada tipo do Pokémon
        for poke_type in pokemon_data['types']:
            if poke_type in defensive_chart:
                chart_data = defensive_chart[poke_type]
                
                all_weaknesses.update(chart_data.get('weak_to', []))
                all_resistances.update(chart_data.get('resist', []))
                all_immunities.update(chart_data.get('immune_to', []))
        
        # Remove sobreposições (imunidade > resistência > fraqueza)
        all_resistances -= all_immunities
        all_weaknesses -= all_immunities
        all_weaknesses -= all_resistances
        
        # Para Pokémons dual-type, calcula interações
        if len(pokemon_data['types']) == 2:
            type1, type2 = pokemon_data['types']
            if type1 in defensive_chart and type2 in defensive_chart:
                # Calcula multiplicadores compostos
                for attack_type in ['Normal', 'Fire', 'Water', 'Electric', 'Grass', 'Ice', 'Fighting', 'Poison', 'Ground', 'Flying', 'Psychic', 'Bug', 'Rock', 'Ghost', 'Dragon', 'Dark', 'Steel', 'Fairy']:
                    multiplier1 = self.get_type_multiplier(attack_type, type1, defensive_chart)
                    multiplier2 = self.get_type_multiplier(attack_type, type2, defensive_chart)
                    total_multiplier = multiplier1 * multiplier2
                    
                    if total_multiplier > 1:
                        all_weaknesses.add(attack_type)
                        all_resistances.discard(attack_type)
                        all_immunities.discard(attack_type)
                    elif total_multiplier < 1 and total_multiplier > 0:
                        if attack_type not in all_weaknesses:
                            all_resistances.add(attack_type)
                        all_immunities.discard(attack_type)
                    elif total_multiplier == 0:
                        all_immunities.add(attack_type)
                        all_weaknesses.discard(attack_type)
                        all_resistances.discard(attack_type)
        
        pokemon_data["type_effectiveness"]["weaknesses"] = list(all_weaknesses)
        pokemon_data["type_effectiveness"]["resistances"] = list(all_resistances)
        pokemon_data["type_effectiveness"]["immunities"] = list(all_immunities)
        
        self.logger.info(f"{pokemon_data['name']} calculado: Fraco a {list(all_weaknesses)}, Resiste a {list(all_resistances)}, Imune a {list(all_immunities)}")
    
    def get_type_multiplier(self, attacking_type, defending_type, chart):
        """Retorna o multiplicador de dano para uma combinação de tipos"""
        if defending_type not in chart:
            return 1.0
        
        defend_data = chart[defending_type]
        
        if attacking_type in defend_data.get('immune_to', []):
            return 0.0
        elif attacking_type in defend_data.get('weak_to', []):
            return 2.0
        elif attacking_type in defend_data.get('resist', []):
            return 0.5
        else:
            return 1.0
    
    def calculate_offensive_effectiveness(self, pokemon_data):
        """Calcula a efetividade ofensiva baseada nos tipos do Pokémon"""
        
        # Tabela de efetividade de tipos (simplificada - os principais)
        type_chart = {
            'Normal': {
                'weak_against': ['Rock', 'Steel'],
                'no_effect': ['Ghost']
            },
            'Fire': {
                'strong_against': ['Grass', 'Ice', 'Bug', 'Steel'],
                'weak_against': ['Fire', 'Water', 'Rock', 'Dragon']
            },
            'Water': {
                'strong_against': ['Fire', 'Ground', 'Rock'],
                'weak_against': ['Water', 'Grass', 'Dragon']
            },
            'Electric': {
                'strong_against': ['Water', 'Flying'],
                'weak_against': ['Electric', 'Grass', 'Dragon'],
                'no_effect': ['Ground']
            },
            'Grass': {
                'strong_against': ['Water', 'Ground', 'Rock'],
                'weak_against': ['Fire', 'Grass', 'Poison', 'Flying', 'Bug', 'Dragon', 'Steel']
            },
            'Ice': {
                'strong_against': ['Grass', 'Ground', 'Flying', 'Dragon'],
                'weak_against': ['Fire', 'Water', 'Ice', 'Steel']
            },
            'Fighting': {
                'strong_against': ['Normal', 'Ice', 'Rock', 'Dark', 'Steel'],
                'weak_against': ['Poison', 'Flying', 'Psychic', 'Bug', 'Fairy'],
                'no_effect': ['Ghost']
            },
            'Poison': {
                'strong_against': ['Grass', 'Fairy'],
                'weak_against': ['Poison', 'Ground', 'Rock', 'Ghost'],
                'no_effect': ['Steel']
            },
            'Ground': {
                'strong_against': ['Fire', 'Electric', 'Poison', 'Rock', 'Steel'],
                'weak_against': ['Grass', 'Bug'],
                'no_effect': ['Flying']
            },
            'Flying': {
                'strong_against': ['Electric', 'Fighting', 'Bug', 'Grass'],
                'weak_against': ['Electric', 'Rock', 'Steel']
            },
            'Psychic': {
                'strong_against': ['Fighting', 'Poison'],
                'weak_against': ['Psychic', 'Steel'],
                'no_effect': ['Dark']
            },
            'Bug': {
                'strong_against': ['Grass', 'Psychic', 'Dark'],
                'weak_against': ['Fire', 'Fighting', 'Poison', 'Flying', 'Ghost', 'Steel', 'Fairy']
            },
            'Rock': {
                'strong_against': ['Fire', 'Ice', 'Flying', 'Bug'],
                'weak_against': ['Fighting', 'Ground', 'Steel']
            },
            'Ghost': {
                'strong_against': ['Psychic', 'Ghost'],
                'weak_against': ['Dark'],
                'no_effect': ['Normal']
            },
            'Dragon': {
                'strong_against': ['Dragon'],
                'weak_against': ['Steel'],
                'no_effect': ['Fairy']
            },
            'Dark': {
                'strong_against': ['Psychic', 'Ghost'],
                'weak_against': ['Fighting', 'Dark', 'Fairy']
            },
            'Steel': {
                'strong_against': ['Ice', 'Rock', 'Fairy'],
                'weak_against': ['Fire', 'Water', 'Electric', 'Steel']
            },
            'Fairy': {
                'strong_against': ['Fighting', 'Dragon', 'Dark'],
                'weak_against': ['Fire', 'Poison', 'Steel']
            }
        }
        
        # Para cada tipo do Pokémon, adiciona as efetividades
        for poke_type in pokemon_data['types']:
            if poke_type in type_chart:
                chart_data = type_chart[poke_type]
                
                # Super efetivo (2x damage)
                if 'strong_against' in chart_data:
                    for target_type in chart_data['strong_against']:
                        if target_type not in pokemon_data["type_effectiveness"]["super_effective_against"]:
                            pokemon_data["type_effectiveness"]["super_effective_against"].append(target_type)
                
                # Pouco efetivo (0.5x damage)
                if 'weak_against' in chart_data:
                    for target_type in chart_data['weak_against']:
                        if target_type not in pokemon_data["type_effectiveness"]["not_very_effective_against"]:
                            pokemon_data["type_effectiveness"]["not_very_effective_against"].append(target_type)
                
                # Sem efeito (0x damage)
                if 'no_effect' in chart_data:
                    for target_type in chart_data['no_effect']:
                        if target_type not in pokemon_data["type_effectiveness"]["no_effect_against"]:
                            pokemon_data["type_effectiveness"]["no_effect_against"].append(target_type)

    def closed(self, reason):
        # Verifica se há Pokémons com habilidades pendentes e os salva mesmo assim
        if self.pending_abilities:
            self.logger.warning(f"Salvando {len(self.pending_abilities)} Pokémons com habilidades incompletas")
            for pokemon_key, pending_info in self.pending_abilities.items():
                pokemon_data = pending_info['pokemon_data']
                pokemon_data['abilities'] = pending_info['abilities']  # Salva o que conseguiu coletar
                self.all_pokemons_data.append(pokemon_data)

        # Salva todos os dados
        with open("pokemons_final.json", "w", encoding="utf-8") as f:
            json.dump(self.all_pokemons_data, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"CONCLUÍDO! Total de Pokémons salvos: {len(self.all_pokemons_data)}")
        
        # Log adicional para debug
        numbers = [int(p['number']) for p in self.all_pokemons_data if p['number'].isdigit()]
        if numbers:
            self.logger.info(f"Números coletados: {min(numbers)} até {max(numbers)}")


# ===== Executa o spider =====
if __name__ == "__main__":
    settings = {
        "LOG_LEVEL": "INFO",
        "FEED_EXPORT_ENCODING": "utf-8",
        "DOWNLOAD_DELAY": 0.5,  # Reduzido para acelerar
        "RANDOMIZE_DOWNLOAD_DELAY": 0.3,
        "CONCURRENT_REQUESTS": 8,  # Permite mais requisições simultâneas
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 0.5,
        "AUTOTHROTTLE_MAX_DELAY": 3,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        "DOWNLOAD_TIMEOUT": 30
    }
    
    process = CrawlerProcess(settings)
    process.crawl(PokedexFinalSpider)
    process.start()