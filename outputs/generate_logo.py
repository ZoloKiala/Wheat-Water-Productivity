# Hub Uploader logo generator
# Runs anywhere Python runs, including Google Colab.
# In Colab, add at the end:
#   from google.colab import files
#   files.download('hub_uploader_icon.svg')

ICON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0b1424"/>
      <stop offset="1" stop-color="#101d33"/>
    </linearGradient>
    <linearGradient id="drop" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#34e3c2"/>
      <stop offset="1" stop-color="#14b88a"/>
    </linearGradient>
  </defs>
  <!-- app tile -->
  <rect x="16" y="16" width="480" height="480" rx="112" fill="url(#bg)"/>
  <rect x="16" y="16" width="480" height="480" rx="112" fill="none"
        stroke="#2dd4bf" stroke-opacity="0.25" stroke-width="6"/>
  <!-- droplet -->
  <path d="M256 88
           C256 88 128 236 128 328
           A128 128 0 0 0 384 328
           C384 236 256 88 256 88 Z"
        fill="url(#drop)"/>
  <!-- upload arrow (cut into droplet) -->
  <path d="M256 232 L322 310 H284 V400 H228 V310 H190 Z"
        fill="#0b1424"/>
  <!-- highlight -->
  <ellipse cx="196" cy="200" rx="26" ry="52"
           transform="rotate(24 196 200)" fill="#ffffff" opacity="0.28"/>
</svg>'''

WORDMARK = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 280" width="1000" height="280">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0b1424"/>
      <stop offset="1" stop-color="#101d33"/>
    </linearGradient>
    <linearGradient id="drop" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#34e3c2"/>
      <stop offset="1" stop-color="#14b88a"/>
    </linearGradient>
  </defs>
  <!-- icon -->
  <g transform="translate(20,20) scale(0.47)">
    <rect x="16" y="16" width="480" height="480" rx="112" fill="url(#bg)"/>
    <rect x="16" y="16" width="480" height="480" rx="112" fill="none"
          stroke="#2dd4bf" stroke-opacity="0.25" stroke-width="6"/>
    <path d="M256 88 C256 88 128 236 128 328 A128 128 0 0 0 384 328 C384 236 256 88 256 88 Z"
          fill="url(#drop)"/>
    <path d="M256 232 L322 310 H284 V400 H228 V310 H190 Z" fill="#0b1424"/>
    <ellipse cx="196" cy="200" rx="26" ry="52"
             transform="rotate(24 196 200)" fill="#ffffff" opacity="0.28"/>
  </g>
  <!-- wordmark -->
  <text x="300" y="150" font-family="Segoe UI, Helvetica, Arial, sans-serif"
        font-size="88" font-weight="700" fill="#eaf2f7">Hub <tspan fill="#2dd4bf">uploader</tspan></text>
  <text x="304" y="205" font-family="Segoe UI, Helvetica, Arial, sans-serif"
        font-size="34" font-weight="400" fill="#8aa0b4"
        letter-spacing="2">IWMI &#8226; Hugging Face Hub</text>
</svg>'''

with open('hub_uploader_icon.svg', 'w') as f:
    f.write(ICON)
with open('hub_uploader_wordmark.svg', 'w') as f:
    f.write(WORDMARK)

print('Saved hub_uploader_icon.svg and hub_uploader_wordmark.svg')
