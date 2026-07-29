/* Supporting content published alongside the dashboard (ToR WP5): purpose,
   methodology, data sources, user guidance, citation and disclaimers. */

export const PAGES = {
  method: {
    title: 'Methodology',
    body: (
      <>
        <p>The Wheat Water Productivity Tool (WWPT) estimates wheat water productivity from
          FAO WaPOR v3 remote-sensing data at 100 m resolution.</p>
        <h4>Analytical pipeline</h4>
        <ol>
          <li>Seasonal Net Primary Production (NPP) is retrieved from WaPOR for the selected
            season and extent.</li>
          <li>NPP is converted to above-ground biomass and grain yield using crop-specific
            harvest index and moisture parameters.</li>
          <li>Water productivity is computed as grain yield per unit of actual
            evapotranspiration (kg/m³).</li>
          <li>A LightGBM regression model, trained on ground data from Oromia and Afar regional
            states, predicts productivity from biophysical and socioeconomic variables and
            quantifies the drivers of spatial variability.</li>
        </ol>
        <h4>Explanatory variables</h4>
        <p>Seasonal NPP, seasonal rainfall, actual evapotranspiration, soil organic carbon,
          elevation, fertilizer applied (NPS), planting dekad, improved seed use, extension
          visits, and distance to market.</p>
        <h4>Validation</h4>
        <p>Model outputs are validated against field observations from major wheat-producing
          areas: irrigated field boundaries, yield records, planting and harvesting dates, and
          agronomic management practices. Holdout performance for the active model version is
          reported under <b>Model</b> in the results panel.</p>
      </>
    ),
  },
  data: {
    title: 'Data sources',
    body: (
      <>
        <p><b>FAO WaPOR v3</b> — Net Primary Production, actual evapotranspiration and
          interception, 100 m, dekadal. Developed with financial support from the Government of
          the Netherlands.</p>
        <p><b>Ground observations</b> — field boundaries, yield and production records, and
          management practices collected by IWMI, EIAR and partners in Oromia and Afar regional
          states.</p>
        <p><b>Ancillary layers</b> — CSA administrative boundaries, soil properties (EthioSIS),
          elevation (SRTM 30 m), and household survey variables.</p>
      </>
    ),
  },
  guide: {
    title: 'User guide',
    body: (
      <>
        <ol>
          <li>Select an area of interest: choose an administrative unit, upload a zipped
            shapefile or GeoJSON of your field or scheme boundary, or draw a polygon directly
            on the map.</li>
          <li>Choose the production system (rainfed or irrigated) and the season to analyse.</li>
          <li>Select <b>Run analysis</b>. The tool retrieves WaPOR NPP, computes water
            productivity at 100 m, and runs the LightGBM prediction model.</li>
          <li>Explore the map. Click any pixel to see its value, then choose <b>Explain this
            prediction</b> to see which factors drove it.</li>
          <li>Export results as CSV for further analysis.</li>
        </ol>
        <h4>Upload requirements</h4>
        <ul>
          <li>Zipped shapefile (<code>.shp</code>, <code>.shx</code>, <code>.dbf</code>,
            <code>.prj</code> in one <code>.zip</code>) or a GeoJSON file.</li>
          <li>Coordinate reference system EPSG:4326 (WGS 84 geographic).</li>
          <li>Maximum 20 MB; extent limited to about 3° × 3°.</li>
        </ul>
        <h4>Reading the charts</h4>
        <p>Every chart has a <b>Show table</b> link beneath it that lists the same values as
          text, so no figure depends on colour or on hovering.</p>
      </>
    ),
  },
  cite: {
    title: 'How to cite',
    body: (
      <>
        <p>IWMI &amp; EIAR (2026). <i>Wheat Water Productivity Dashboard for Ethiopia.</i>
          Developed under the WaPOR Phase II project with FAO. Ethiopian Institute of
          Agricultural Research, Addis Ababa.</p>
        <p>Data: FAO. <i>WaPOR — Water Productivity through Open access of Remotely sensed
          derived data</i>, version 3.</p>
      </>
    ),
  },
  disclaimer: {
    title: 'Disclaimer',
    body: (
      <>
        <p>This dashboard provides model-based estimates derived from remote sensing and machine
          learning. Values are indicative and intended for research, planning and advisory
          purposes. They do not replace field measurement.</p>
        <p>Administrative boundaries shown do not imply official endorsement. The designations
          employed do not imply any opinion on the legal status of any country, territory or
          area.</p>
      </>
    ),
  },
}
