# Carte de fraîcheur — Paris 8ᵉ

Prototype de **Single Page Application** cartographiant la **chaleur ressentie**
dans le 8ᵉ arrondissement de Paris, en fonction de l'heure de la journée et d'un
scénario météo (Normal / Canicule).

Application 100 % statique : **HTML5 + CSS3 + JavaScript ES6**, cartographie via
**Leaflet.js** (CDN) et fond de carte épuré **CartoDB Positron**. Aucun framework
ni outil de build.

## 🚀 Lancer l'application

Les données sont chargées par `fetch()` : il faut servir le dossier via HTTP
(le protocole `file://` est bloqué par la politique CORS des navigateurs).

```bash
python3 -m http.server 8000
# puis ouvrir http://localhost:8000
```

## 🧊 Préparation des données (`filter_data.py`)

Les fichiers GeoJSON officiels de Paris (rues, espaces verts, îlots de fraîcheur,
arbres) sont **volumineux** et couvrent tout Paris. Le bâti national
(`bati_paris.geojson`, > 30 Mo) ne doit jamais être stocké tel quel.

Le script `filter_data.py` (Python 3 **sans dépendance**) :

1. récupère le **polygone officiel du 8ᵉ** (Paris Open Data) ;
2. récupère **uniquement le bâti du 8ᵉ**, filtré côté serveur via l'API
   (`where=n_ar=8`) — le fichier lourd n'est jamais téléchargé en entier ;
3. **découpe** les 4 GeoJSON fournis sur le 8ᵉ (attribut d'arrondissement +
   test géométrique point-dans-polygone) ;
4. **précalcule** pour chaque tronçon de rue :
   - `h_bati` : hauteur du bâti voisin (≈ nb. de niveaux × 3 m) ;
   - `cool` / `cool_dist` : proximité (≤ 30 m) d'un parc, d'un îlot de fraîcheur
     ou d'un arbre remarquable ;
5. écrit les fichiers **allégés** dans `data/` (~ 0,8 Mo au total contre ~ 36 Mo
   de sources).

```bash
# RAW_DIR = dossier contenant les 4 GeoJSON bruts fournis
RAW_DIR=/chemin/vers/mes/geojson python3 filter_data.py
```

Sorties dans `data/` : `arrondissement_8e.geojson`, `troncon_8e.geojson`,
`espaces_verts_8e.geojson`, `ilots_fraicheur_8e.geojson`, `arbres_8e.geojson`,
`meta.json`.

## 🌡️ Modèle de chaleur ressentie

Pour chaque tronçon de rue :

```
T_ressentie = T_base
            + Modificateur_Ombre(heure, h_bati)
            + Modificateur_Bitume
            - Modificateur_Fraicheur
```

| Composante | Règle |
|---|---|
| **T_base** | Normal **28 °C**, Canicule **38 °C** (réf. station Montsouris) |
| **Ombre / soleil** | Position du soleil simulée par l'heure (élévation + azimut). On compare la longueur de l'ombre portée par le bâti voisin à la largeur de rue : rue **ombragée → +2 °C** ; rue **ensoleillée → jusqu'à +12 °C** en plein midi (interpolé selon l'élévation solaire). |
| **Bitume** | **+2,5 °C** constant sur toutes les rues (asphalte). |
| **Fraîcheur** | **−4,5 °C** si la rue est à moins de 30 m d'un espace vert, d'un îlot de fraîcheur ou d'un arbre remarquable (évapotranspiration). |

Les relations spatiales statiques (`h_bati`, `cool`) sont précalculées par le
script ; le navigateur n'effectue que l'arithmétique dépendante de l'heure et du
scénario, ce qui garde l'interface fluide même sur ~ 1 000 tronçons.

Les rues sont colorées sur un dégradé **bleu/vert (frais) → rouge/violet (très
chaud)**. Un clic sur une rue détaille le calcul.

## 🎛️ Interface

- **Slider Heure** : 08 h → 20 h (pas de 1 h).
- **Scénario météo** : Normal / Canicule.
- **Position du soleil** simulée (élévation, azimut).
- **Calques** activables : rues (chaleur), espaces verts, îlots de fraîcheur,
  arbres remarquables.
- **Légende** et statistiques de l'arrondissement.

## ⚠️ Avertissement

Modèle **simplifié et pédagogique** : il s'agit d'une *approximation* destinée à
la visualisation, et non d'une mesure micro-climatique réelle.

## 📁 Structure

```
index.html          # structure (sidebar + carte)
style.css           # mise en forme
app.js              # carte Leaflet, modèle thermique, interactions
filter_data.py      # préparation / allègement des données
data/               # GeoJSON filtrés (générés par le script)
```

## Sources

[Paris Open Data](https://opendata.paris.fr) — tronçons de voie, espaces verts,
îlots de fraîcheur, arbres remarquables, volumes bâtis, arrondissements.
