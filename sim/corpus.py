"""Corpus fictif de Rive-Claire : la base de connaissances interrogée par les agents (RAG, exigence F1).
Sources variées : mairie, plan du quartier, archives de la messagerie de voisinage, retours de la crue de 2019.
Certaines informations sont périmées ou fausses (reliable=False) : un bon agent vérifie la source et la date.
site = sujet principal (pont, passage, ecole, gymnase, quais, general) ; topic sert à évaluer la récupération (F2), jamais à filtrer."""
def D(id, site, topic, year, source, text, reliable=True): return {"id": id, "site": site, "topic": topic, "year": year, "source": source, "reliable": reliable, "text": text}

DOCS = [
 D("plan-01", "general", "acces", 2025, "plan du quartier", "Rive-Claire : le quartier des Quais est sur la rive sud. Les deux refuges sont l'École des Tilleuls (rive sud, ouest) et le Gymnase des Hauteurs (rive nord, est)."),
 D("plan-02", "pont", "acces", 2025, "plan du quartier", "Le Pont Central relie la place du quartier à la rue Haute. C'est le chemin le plus court vers le Gymnase des Hauteurs : 18 m de pont, chaussée de 5,5 m, sans marche."),
 D("plan-03", "passage", "acces", 2025, "plan du quartier", "Le Passage des Hauteurs franchit la rivière à l'ouest, près de l'école. Il est plus long (24 m) et se termine par des escaliers : il ne convient pas aux personnes qui ne peuvent pas monter de marches."),
 D("plan-04", "quais", "inondation", 2025, "plan de prévention", "Les rues basses des Quais, surtout la rue des Quais côté ouest et l'accès au Passage des Hauteurs, sont les premières touchées en cas de débordement."),
 D("refuge-01", "ecole", "capacite", 2025, "mairie", "École des Tilleuls : refuge de proximité, parvis surélevé de 1,2 m, capacité de 7 personnes pour ce scénario. Accès par la rue de l'Ouest."),
 D("refuge-02", "gymnase", "capacite", 2025, "mairie", "Gymnase des Hauteurs : refuge principal sur la rive nord, hors zone inondable, capacité de 12 personnes pour ce scénario."),
 D("refuge-03", "ecole", "inondation", 2024, "plan de prévention", "Le secteur de l'école est construit sur une ancienne boucle de la rivière : les rues qui y mènent peuvent être inondées avant le reste du quartier."),
 D("refuge-04", "gymnase", "capacite", 2018, "ancien affichage", "Ancien affichage de 2018 : le gymnase est fermé pour travaux et n'accueille personne. Ces travaux sont terminés depuis 2020.", False),
 D("pont-01", "pont", "fermeture", 2025, "mairie", "Règle municipale : le Pont Central est fermé à la circulation dès que la mairie l'annonce, en général quand l'eau approche le tablier. Seul un bulletin officiel de la mairie confirme une fermeture."),
 D("pont-02", "pont", "rumeur", 2021, "archives de la messagerie", "Archive de la messagerie, 2021 : un message anonyme annonçait « Pont Central fermé ». C'était faux ; plusieurs voisins ont fait le détour par les escaliers du Passage pour rien.", True),
 D("pont-03", "pont", "fermeture", 2019, "retour d'expérience", "Crue de 2019 : le Pont Central est resté ouvert jusqu'à la fin de l'évacuation ; c'est la rue des Quais qui a été coupée en premier."),
 D("pont-04", "pont", "rumeur", 2023, "messagerie de voisinage", "Message de 2023 sans auteur : « le pont s'effondre à chaque crue, ne le prenez jamais ». Aucune source, aucune trace d'incident.", False),
 D("alerte-01", "general", "alerte", 2025, "mairie", "Niveaux d'alerte de la mairie : vigilance (se tenir prêt), alerte (préparer son départ), évacuation (partir maintenant vers un refuge accessible)."),
 D("alerte-02", "general", "alerte", 2025, "mairie", "Les bulletins officiels sont émis par le poste municipal, place du quartier. Ils portent toujours l'heure et la signature de la mairie."),
 D("alerte-03", "general", "verification", 2025, "mairie", "Pour vérifier une information : appeler le poste municipal ou regarder soi-même depuis la place. Ne relayez pas un message sans source."),
 D("crue-01", "quais", "souvenir", 2019, "retour d'expérience", "Crue de 2019 : l'eau a atteint 40 cm rue des Quais en moins d'une heure. Les habitants partis tôt sont arrivés sans difficulté ; ceux partis après l'alerte ont marché dans l'eau."),
 D("crue-02", "quais", "souvenir", 2019, "retour d'expérience", "En 2019, plusieurs habitants ont attendu de voir partir leurs voisins avant de bouger. Le retard moyen au départ a été de vingt minutes."),
 D("crue-03", "general", "souvenir", 2019, "journal local", "Au-delà de 50 cm d'eau, on ne marche plus en sécurité dans la rue : le courant fait perdre l'équilibre. Entre 20 et 50 cm, on avance très lentement."),
 D("meteo-01", "general", "meteo", 2026, "service météo", "Bulletin météo du matin : fortes pluies en amont pendant la nuit, la rivière va monter dans la matinée. Vigilance crue pour Rive-Claire."),
 D("meteo-02", "general", "meteo", 2026, "service météo", "Prévision : pic de crue attendu vers 10 h. L'ampleur dépend des pluies en amont ; un débordement sur les Quais est possible dès 9 h 10."),
 D("voisins-01", "general", "reseau", 2025, "messagerie de voisinage", "La messagerie de voisinage relaie vite, mais les messages perdent souvent leur source. Indiquer qui a vu quoi, et à quelle heure."),
 D("voisins-02", "passage", "acces", 2024, "messagerie de voisinage", "Omar conseille le détour par les Hauteurs quand le pont est encombré ; il rappelle aussi que l'accès au Passage est bas et s'inonde vite."),
 D("cafe-01", "quais", "acces", 2025, "plan du quartier", "Le café de la rue des Quais est dans un point bas ; l'eau y entre tôt. Le personnel rejoint le gymnase par le Pont Central."),
 D("soins-01", "general", "mobilite", 2025, "centre social", "Personnes à mobilité réduite : privilégier un itinéraire sans escalier (Pont Central) et partir tôt ; prévoir un accompagnant."),
]

from world import EXTENDED
if EXTENDED:                                                    # MONDE=etendu : quartiers Ouest et Est, trois refuges de plus
    DOCS += [
     D("plan-05", "general", "acces", 2025, "plan du quartier", "Rive-Claire s'étend aussi à l'ouest (rue des Peupliers, rue du Lavoir) et à l'est (rue du Moulin-Neuf, rue des Vergers). La mairie oriente l'ouest du quartier vers l'Église Saint-Roch et l'est vers le Collège du Plateau ; le Gymnase des Hauteurs reste le refuge principal du centre."),
     D("refuge-05", "eglise", "capacite", 2025, "mairie", "Église Saint-Roch : refuge de la rive nord, à l'ouest, au bout de la rue Haute, hors zone inondable, capacité de 40 personnes. Accès le plus court par le Passage des Hauteurs."),
     D("refuge-06", "college", "capacite", 2025, "mairie", "Collège du Plateau : refuge de la rive nord, à l'est, par la rue de la Berge, hors zone inondable, capacité de 40 personnes. Il faut franchir la rivière par le Pont Central ou le Passage."),
     D("refuge-07", "salle", "inondation", 2024, "plan de prévention", "Salle des Fêtes du Moulin : refuge d'appoint de la rive sud, quartier Est, 15 places, sur la butte du Moulin. Plus haute que les Quais, mais les rues qui y mènent peuvent être inondées par une forte crue."),
    ]
