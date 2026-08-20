/* Supporting content published alongside the dashboard (ToR WP5): purpose,
   methodology, data sources, user guidance, citation and disclaimers.

   The methodology page describes the method actually implemented in
   backend/app/wwpt.py, ported from the IWMI reference notebook. Keep the two in
   step: if the equations or the crop parameters change there, they change here. */

export const PAGES = {
  method: {
    title: 'Methodology',
    body: (
      <>
        <p>The Wheat Water Productivity Tool (WWPT) estimates wheat biomass, grain yield and
          water productivity from FAO WaPOR v3 remote-sensing data. It
          implements the method of the IWMI reference notebook
          <i> ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final</i>, which is the authoritative
          version of the calculation.</p>

        <h4>Analytical pipeline</h4>
        <ol>
          <li>Seasonal Net Primary Production (NPP) and Actual Evapotranspiration and
            Interception (AETI) are retrieved from WaPOR v3 and summed over the growing
            season, from start of season (SOS) to end of season (EOS).</li>
          <li>NPP is converted to total biomass using the above-ground biomass ratio, the
            light-use-efficiency correction factor and the moisture content of fresh
            biomass.</li>
          <li>Total biomass is converted to harvestable grain yield using the harvest
            index.</li>
          <li>Water productivity is grain yield divided by seasonal water consumption,
            expressed in kg of grain per m³ of water.</li>
        </ol>

        <h4>Equations</h4>
        <p>
          <code>TB = AOT · fc · NPP · 22.222 / (1 − mc)</code> — total biomass, kg dry
          matter/ha<br />
          <code>Y = TB · hi</code> — grain yield, kg/ha<br />
          <code>CWP = Y / SWC</code>, where <code>SWC = AETI · 10</code> — water
          productivity, kg/m³
        </p>
        <p>NPP is in gC/m²/season and AETI in mm/season. The factor 22.222 converts gC/m²
          to kg dry matter per hectare: 1 gC/m² is 10 kgC/ha, and dry matter is about 45%
          carbon. One millimetre of water over a hectare is 10 m³.</p>

        <h4>Crop parameters</h4>
        <p>AOT is the above-ground over total biomass ratio, <i>fc</i> the
          light-use-efficiency correction factor, <i>mc</i> the moisture content of the
          fresh biomass and <i>hi</i> the harvest index. The values in use are shown in the
          <b> How this estimate is built</b> panel with every result, and are also served at
          <code> /api/method</code>.</p>
        <p>The current values are FAO reference values for wheat, calibrated so that the
          tool reproduces every result published in the reference notebook. As that notebook
          notes, general FAO parameters do not fully reflect wheat grown across Ethiopia's
          agro-ecological zones. Locally derived parameters — from EIAR field trials and
          observed yields — should replace them as they become available; the tool reads
          them from a configuration file precisely so this can be done without changing
          code.</p>

        <h4>What this tool does not do</h4>
        <p>The estimate is deterministic: the same inputs always give the same answer, and
          the derivation is shown in full for any pixel you select. There is no statistical
          model and no fitted coefficients, so there is nothing hidden between the satellite
          measurement and the reported value — but equally, nothing corrects for factors the
          equations do not represent, such as variety, pest damage or post-harvest loss.</p>

        <h4>Validation</h4>
        <p>The port is checked against the reference notebook on every one of the results it
          publishes for the 2026 Ethiopian irrigation schemes, at the notebook's own
          precision (<code>tests/test_notebook_parity.py</code>). Validation against
          <i>field-measured</i> yields is a separate exercise and remains outstanding;
          reported values should be treated as estimates until it is done.</p>
      </>
    ),
  },
  data: {
    title: 'Data sources',
    body: (
      <>
        <p><b>FAO WaPOR v3</b> — Net Primary Production (NPP) and Actual Evapotranspiration
          and Interception (AETI), dekadal, summed across the growing season. The tool reads
          the level 2 national products at 100 m, the same mapsets the WWPT reference
          implementation reads. Developed with financial support from the Government of
          the Netherlands.</p>
        <p><b>Growing season</b> — the season selector resolves to explicit SOS and EOS
          dates, shown with every result. Meher runs June–November, Belg February–June, and
          the irrigated dry season November–March. Where scheme-specific SOS and EOS dates
          are known, they should be used in preference to these defaults.</p>
        <p><b>Crop parameters</b> — the FAO (2020b) reference values for wheat used by the
          WWPT tool: above-ground fraction 0.85, light-use-efficiency correction 0.90, grain
          moisture content 0.15 and harvest index 0.48. To be superseded by EIAR-derived
          parameters for Ethiopian agro-ecological zones.</p>
        <p><b>Ancillary layers</b> — CSA administrative boundaries for area selection.</p>
        <p><b>Demonstration data</b> — when the service runs without WaPOR access it uses a
          built-in synthetic provider so the interface is fully usable offline. Results are
          then labelled <i>Demonstration data</i> in the results panel and in the footer. The
          method is identical in both modes; only the inputs differ.</p>
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
          <li>Choose the production system (rainfed or irrigated) and the season to analyse.
            The season resolves to explicit SOS and EOS dates, shown with the results.</li>
          <li>Select <b>Run analysis</b>. The tool retrieves seasonal WaPOR NPP and AETI and
            computes biomass, yield and water productivity.</li>
          <li>Explore the map. Click any pixel to see its value, then choose <b>Show the
            derivation</b> to see every input, parameter and intermediate behind it.</li>
          <li>Export results as CSV for further analysis. The export uses the same column
            names as the reference notebook (<code>NPP</code>, <code>AETI_mm</code>,
            <code> EYield_tpha</code>, <code>WP_kgpm3</code>) so the two are directly
            comparable.</li>
        </ol>

        <h4>Upload requirements</h4>
        <ul>
          <li>Zipped shapefile (<code>.shp</code>, <code>.shx</code>, <code>.dbf</code>,
            <code>.prj</code> in one <code>.zip</code>) or a GeoJSON file.</li>
          <li>Coordinate reference system EPSG:4326 (WGS 84 geographic).</li>
          <li>Maximum 20 MB. A single analysis extent is limited to about 3° × 3°; a
            scheme file estimated plot by plot has no such limit, since each plot is
            estimated where it sits.</li>
        </ul>

        <h4>Estimating a whole scheme, plot by plot</h4>
        <p>If the file you upload carries a growing season on every feature, the dashboard
          offers <b>Estimate each plot</b> instead of one analysis over a merged extent. Each
          plot or sample point is then estimated over its own season, exactly as the reference
          notebook does it, and the results appear as two tables and two figures: estimated
          yield per scheme, water productivity per scheme, one row per feature, and — for
          sample points — one row per plot using the median of its samples, so a single
          unrepresentative sample cannot move the plot value.</p>
        <p>Required attribute fields:</p>
        <ul>
          <li><code>ID</code> — a unique whole number per feature.</li>
          <li><code>SOS</code> and <code>EOS</code> — start and end of season as dates, with
            EOS after SOS. These define the window over which WaPOR data are summed.</li>
          <li><code>Location</code> — required for point files only: the plot each sample
            belongs to, so samples can be grouped back to it.</li>
          <li><code>Name</code> and <code>Scheme_ID</code> are optional but recommended; they
            label the figures and group the per-plot table.</li>
        </ul>
        <p>Any other attributes you upload are carried through to the results and the CSV
          untouched. If a required field is missing, the dashboard says which one before you
          run anything.</p>

        <h4>Reading the charts</h4>
        <p>Every chart has a <b>Show table</b> link beneath it that lists the same values as
          text, so no figure depends on colour or on hovering. The estimation chain is text
          already — each figure in it can be checked by hand against the equations on the
          Methodology page.</p>
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
        <p>Method: IWMI (2026). <i>WaPOR Based Wheat Yield &amp; Wheat Water Productivity
          Tool for Ethiopia</i> (<code>ETH_WWP_WaPORv3_Irrigaed_Wheat_2026_Final</code>).</p>
        <p>Data: FAO. <i>WaPOR — Water Productivity through Open access of Remotely sensed
          derived data</i>, version 3.</p>
      </>
    ),
  },
  disclaimer: {
    title: 'Disclaimer',
    body: (
      <>
        <p>This dashboard provides estimates derived from satellite remote sensing and
          published crop parameters. Values are indicative and intended for research,
          planning and advisory purposes. They do not replace field measurement.</p>
        <p>Accuracy depends on the quality of the WaPOR data, on the crop parameters used and
          on the assumption that the selected season matches the actual growing period of the
          fields analysed. The general FAO parameters currently in use are not specific to
          Ethiopian conditions.</p>
        <p>Administrative boundaries shown do not imply official endorsement. The designations
          employed do not imply any opinion on the legal status of any country, territory or
          area.</p>
      </>
    ),
  },
}
