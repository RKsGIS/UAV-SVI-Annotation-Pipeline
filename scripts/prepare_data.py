all_uav = pd.read_json('../cache_fast_bbox/oam/all_uav.json')
all_uav['geometry'] = all_uav['geojson'].apply(shape)
all_uav = gpd.GeoDataFrame(all_uav,geometry='geometry',crs='EPSG:4326')


# Save as GeoPackage
out = '../cache_fast_bbox/oam/all_uav.gpkg'

all_uav.to_file(out,driver='GPKG')



scene_enrichment_scores = pd.read_csv('../scene_enrichment_scores.csv')

# Match all_uav._id -> scene_enrichment_scores.scene_id
matched = all_uav.merge(
    scene_enrichment_scores,
    left_on='_id',
    right_on='scene_id',
    how='inner',
    suffixes=('_uav', '_los')
)

# Convert GeoJSON dict to Shapely geometry
matched['geometry'] = matched['geojson'].apply(shape)

# Create GeoDataFrame
matched_gdf = gpd.GeoDataFrame(
    matched,
    geometry='geometry',
    crs='EPSG:4326'
)
matched_gdf
import geopandas as gpd
import pandas as pd
import networkx as nx
from pathlib import Path

# ─── CONFIGURATION ───
ISLAND_MATCH_CAP = 250  # Maximum matches we will keep from any single overlapping cluster

# # ─── 1. Load Data & Hardcode Missing Continents ───
# matched_gdf = gpd.read_file('../pre_dataset.gpkg')

if '_id' in matched_gdf.columns and 'scene_id' not in matched_gdf.columns:
    matched_gdf['scene_id'] = matched_gdf['_id'].astype(str)
else:
    matched_gdf['scene_id'] = matched_gdf['scene_id'].astype(str)

country_col = 'country' if 'country' in matched_gdf.columns else 'NAME_2'

matched_gdf['valid_los_matches'] = matched_gdf['valid_los_matches'].fillna(0).astype(int)

# ─── 2. Build Overlap Graph (Islands) ───
overlaps = gpd.sjoin(
    matched_gdf[['scene_id', 'geometry']], 
    matched_gdf[['scene_id', 'geometry']], 
    how='inner', predicate='intersects'
)

G = nx.Graph()
for sid in matched_gdf['scene_id']:
    G.add_node(sid)

for _, row in overlaps.iterrows():
    s1 = row['scene_id_left']
    s2 = row['scene_id_right']
    if s1 != s2:
        G.add_edge(s1, s2)

clusters = list(nx.connected_components(G))

# ─── 3. Aggregate Island Statistics & CAP MASSIVE CLUSTERS ───
islands = []
discarded_scenes = 0

for idx, cluster in enumerate(clusters):
    cluster_scenes = list(cluster)
    # Get all scenes in this cluster, sorted by highest match count first
    sub = matched_gdf[matched_gdf['scene_id'].isin(cluster_scenes)].sort_values(by='valid_los_matches', ascending=False)
    
    kept_scenes = []
    running_sum = 0
    
    for _, row in sub.iterrows():
        # If we already have enough matches from this overlapping cluster, discard the rest
        if running_sum >= ISLAND_MATCH_CAP and len(kept_scenes) > 0:
            discarded_scenes += 1
            continue
            
        kept_scenes.append(row['scene_id'])
        running_sum += row['valid_los_matches']
    
    # Filter 'sub' to only the scenes we decided to keep
    sub = sub[sub['scene_id'].isin(kept_scenes)]
    
    if running_sum > 0:
        islands.append({
            'island_id': idx,
            'scenes': kept_scenes,
            'scene_count': len(kept_scenes),
            'total_matches': int(sub['valid_los_matches'].sum()),
            'continents': list(sub['continent'].dropna().unique()),
            'countries': list(sub[country_col].dropna().unique()),
            'primary_continent': sub['continent'].mode().iloc[0] if not sub['continent'].empty else 'Unknown'
        })

df_islands = pd.DataFrame(islands)
print(f"Discarded {discarded_scenes} heavily overlapping scenes to enforce geographic diversity.")

# ─── 4. Continent-Stratified Bucket Allocation ───
buckets = {i: {'scenes': [], 'total_matches': 0, 'countries': set(), 'continents': set()} for i in range(1, 11)}

# Iterate through each continent one by one to ensure fair distribution
for continent in df_islands['primary_continent'].unique():
    # Sort the islands in this continent from largest to smallest
    cont_islands = df_islands[df_islands['primary_continent'] == continent].sort_values(by='total_matches', ascending=False)
    
    for _, island in cont_islands.iterrows():
        # Always give the next island to the bucket with the currently lowest total matches
        target_bucket = min(buckets.keys(), key=lambda b: buckets[b]['total_matches'])
        
        buckets[target_bucket]['scenes'].extend(island['scenes'])
        buckets[target_bucket]['total_matches'] += island['total_matches']
        buckets[target_bucket]['countries'].update(island['countries'])
        buckets[target_bucket]['continents'].update(island['continents'])

# ─── 5. Summary & Save Master Assignments ───
print("\n" + "=" * 80)
print("10 STUDENT BUCKET ALLOCATION SUMMARY (Stratified by Continent)")
print("=" * 80)

assignment_records = []
for b_id, b_data in sorted(buckets.items()):
    print(f"Bucket {b_id:02d}: Saved {len(b_data['scenes']):02d} scenes ({b_data['total_matches']} potential matches) -> student_{b_id:02d}_scenes.gpkg")
    print(f"           Continents: {', '.join(sorted(b_data['continents']))}")
    print(f"           Countries:  {', '.join(sorted(b_data['countries']))}")
    print("-" * 80)
    
    for sid in b_data['scenes']:
        assignment_records.append({'scene_id': sid, 'bucket_id': b_id})

df_assign = pd.DataFrame(assignment_records)
matched_gdf = matched_gdf.drop(columns=['bucket_id'], errors='ignore')
# Filter master matched_gdf to ONLY the scenes we assigned (drops the discarded overlaps)
matched_gdf = matched_gdf.merge(df_assign, on='scene_id', how='inner')

matched_gdf.to_file('../pre_dataset.gpkg', driver='GPKG')
matched_gdf.drop(columns=['geometry']).to_csv('../student_scene_assignments.csv', index=False)

# ─── 6. Export Individual Student GeoPackages ───
out_dir = Path('../student_packages')
out_dir.mkdir(parents=True, exist_ok=True)

for b_id in range(1, 11):
    student_gdf = matched_gdf[matched_gdf['bucket_id'] == b_id].copy()
    # columns_to_keep = ['scene_id', 'title', 'acquisition_start', 'gsd', 
    #                    'area_km2', 'total_buildings', 'total_svi_points', 'has_overlap',
    #                    'valid_los_matches', 'country', 'continent', 'geometry']
    
    # existing_columns = [c for c in columns_to_keep if c in student_gdf.columns]
    # student_gdf = student_gdf[existing_columns]
    
    out_file = out_dir / f'student_{b_id:02d}_scenes.gpkg'
    student_gdf.to_file(out_file, driver='GPKG') 