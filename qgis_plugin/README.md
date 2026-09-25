# UAV/SVI Annotation Explorer 0.3.3

- **Display UAV** keeps previously displayed scene rasters. Selecting another OAM scene and displaying it adds a new temporary UAV layer instead of replacing the previous one.
- **Display SVI** requests Mapillary vector tiles from the **selected OAM scene bounding box**, not the current QGIS canvas extent. Older ROI coverage remains visible. Tile resolution is derived from the requested ROI.
- **Display OSM Buildings** requests buildings for the selected scene bbox and keeps older ROI building layers.
- **Calculate Building Directions** follows the heat-resilience preprocessing approach: search for SVI imagery around each building centroid using the configurable metre buffer, retain the nearest SVI, calculate the building/SVI bearing, and highlight only relevant buildings. The highlighted building overlay is styling only, not a mask.
- **Direction visualization:** red line connects building centroid and SVI point; blue arrow starts at the SVI point and follows Mapillary `compass_angle`.
- **Preview Selected Mapillary Image** displays the image directly through the existing Mapillary preview workflow and highlights the relevant building when one falls within the configured buffer. No image masking is performed.
- **QGIS 4 compatibility:** fields use Qt6 `QMetaType.Type` values.
- All exploration outputs remain temporary and are removed by **Clear temporary exploration layers**.
