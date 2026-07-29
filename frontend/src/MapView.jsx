/* Leaflet map: AOI outline, analysis raster overlay, polygon drawing and
   pixel inspection. Leaflet is driven imperatively through refs; React owns
   only the surrounding chrome. */

import { useEffect, useRef } from 'react'
import L from 'leaflet'

export default function MapView({
  aoiOutline,    // {bounds} | {polys} | null — dashed AOI boundary
  raster,        // {url, bounds} | null — analysis result overlay
  fitTo, fitKey, // bounds to fit; refit whenever fitKey changes
  drawing,       // polygon drawing armed
  drawPoints,    // vertices placed so far
  onDrawPoint, onDrawFinish,
  onInspect,     // (lat, lng) click inside a completed run
  inspectEnabled,
  marker,        // {lat, lng} | null
  popup,         // {lat, lng, html} | null
  onExplain,
}) {
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layers = useRef({ aoi: null, raster: null, line: null, marker: null })

  // Latest callbacks, so the once-bound Leaflet handlers never go stale.
  const cb = useRef({})
  cb.current = { onDrawPoint, onDrawFinish, onInspect, inspectEnabled, drawing, onExplain }

  useEffect(() => {
    const map = L.map(elRef.current, { zoomControl: true, doubleClickZoom: false })
      .setView([9.0, 39.6], 6)
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors', maxZoom: 18,
    }).addTo(map)
    map.zoomControl.setPosition('topright')
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map)
    mapRef.current = map

    map.on('click', (e) => {
      const c = cb.current
      if (c.drawing) c.onDrawPoint([e.latlng.lat, e.latlng.lng])
      else if (c.inspectEnabled) c.onInspect(e.latlng.lat, e.latlng.lng)
    })
    map.on('dblclick', () => { if (cb.current.drawing) cb.current.onDrawFinish() })

    // Leaflet only watches the window for resizes, not its own container. The
    // results panel opening or closing resizes .mapwrap without a window
    // resize, which would leave the map rendering at its stale width (blank
    // tile gutters and a misplaced raster overlay) until the next pan.
    const ro = new ResizeObserver(() => map.invalidateSize({ animate: false }))
    ro.observe(elRef.current)

    return () => { ro.disconnect(); map.remove(); mapRef.current = null }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (map) map.getContainer().style.cursor = drawing ? 'crosshair' : ''
  }, [drawing])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (layers.current.aoi) { map.removeLayer(layers.current.aoi); layers.current.aoi = null }
    if (!aoiOutline) return
    const style = { color: '#14432b', weight: 2, dashArray: '6 4', fill: false }
    layers.current.aoi = aoiOutline.polys
      ? L.polygon(aoiOutline.polys, style).addTo(map)
      : L.rectangle(aoiOutline.bounds, style).addTo(map)
  }, [aoiOutline])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (layers.current.raster) { map.removeLayer(layers.current.raster); layers.current.raster = null }
    if (!raster) return
    layers.current.raster = L.imageOverlay(raster.url, raster.bounds, {
      opacity: 1, className: 'pix', interactive: false,
    }).addTo(map)
  }, [raster])

  useEffect(() => {
    const map = mapRef.current
    if (map && fitTo) map.fitBounds(fitTo, { padding: [40, 40] })
  }, [fitKey])

  /* In-progress drawing preview.
     The polyline and vertex markers are updated IN PLACE rather than recreated
     per vertex: replacing the SVG nodes under the cursor changes the pointer's
     target element between the two clicks of a double-click, which stops the
     browser from ever emitting `dblclick` — so finishing the polygon silently
     failed. They are also non-interactive so a click on an existing vertex
     still reaches the map's own click handler. */
  const preview = useRef({ line: null, dots: null, count: 0 })
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const p = preview.current
    if (!drawing) {
      if (p.line) { map.removeLayer(p.line); p.line = null }
      if (p.dots) { map.removeLayer(p.dots); p.dots = null }
      p.count = 0
      return
    }
    if (!p.line) {
      p.line = L.polyline([], { color: '#b07f28', weight: 2.5, interactive: false }).addTo(map)
      p.dots = L.layerGroup().addTo(map)
      p.count = 0
    }
    p.line.setLatLngs(drawPoints)
    for (let i = p.count; i < drawPoints.length; i++) {
      L.circleMarker(drawPoints[i], {
        radius: 3.5, color: '#b07f28', fillColor: '#fff', fillOpacity: 1, weight: 2,
        interactive: false,
      }).addTo(p.dots)
    }
    if (drawPoints.length < p.count) p.dots.clearLayers()
    p.count = drawPoints.length
  }, [drawing, drawPoints])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (layers.current.marker) { map.removeLayer(layers.current.marker); layers.current.marker = null }
    if (!marker) return
    layers.current.marker = L.circleMarker([marker.lat, marker.lng], {
      radius: 6, color: '#fff', weight: 2, fillColor: '#b07f28', fillOpacity: 1,
    }).addTo(map)
  }, [marker])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!popup) { map.closePopup(); return }
    L.popup({ offset: [0, -4] }).setLatLng([popup.lat, popup.lng]).setContent(popup.html).openOn(map)
    // Popup content lives outside React's tree, so bind the action by id.
    const btn = document.getElementById('explainLink')
    if (btn) btn.onclick = () => { cb.current.onExplain(); map.closePopup() }
  }, [popup])

  return <div id="map" ref={elRef} role="application"
    aria-label="Map of wheat water productivity. Use the sidebar controls to select an area and run the analysis." />
}
