"""Label vocabulary shared by packaging, validation, Label Studio import/export and the model."""

LABEL_COLUMNS = ["structural_openness", "number_of_floors", "vegetation", "material_rooftop", "material_wall"]

VOCAB = {
    "structural_openness": ["closed_structure", "partial_structure", "open_structure", "unknown"],
    "number_of_floors": ["one", "two", "three", "four_or_more", "unknown"],
    "vegetation": ["yes", "no"],
    "material_rooftop": ["metal", "concrete", "brick", "wood",   "unknown"],
    "material_wall": ["metal", "concrete", "clay", "tarpaulin", "wood", "unknown"],
}

META_COLUMNS = ["mapillary_id"]
CSV_COLUMNS = ["osm_id", *META_COLUMNS, *LABEL_COLUMNS]
IMAGE_SIZE = 256
