"""Label vocabulary shared by packaging, validation, Label Studio import/export and the model."""

LABEL_COLUMNS = ["structural_openness", "number_of_floors", "vegetation", "material_rooftop", "material_wall"]

VOCAB = {
    "structural_openness": ["closed_structure", "open_structure", "unknown"],
    "number_of_floors": ["one", "two", "three_or_more", "unknown"],
    "vegetation": ["yes", "no"],
    "material_rooftop": ["concrete", "metal", "tile", "asbestos", "thatch_wood", "other", "unknown"],
    "material_wall": ["concrete", "brick", "metal", "wood", "glass", "other", "unknown"],
}

CSV_COLUMNS = ["osm_id", *LABEL_COLUMNS]
IMAGE_SIZE = 256
