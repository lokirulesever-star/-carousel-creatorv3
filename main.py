"""
Carousel Creator — Ponto de entrada principal
Google Gemini 2.0 Flash (texto) + Imagen 3 (imagens)
"""

import base64
import io
import json
import os
import zipfile
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Carousel Creator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL   = "gemini-2.0-flash"
IMAGEN_MODEL   = "imagen-3.0-generate-001"


# ─── Models ────────────────────────────────────────────────────────────────────

class TextGenRequest(BaseModel):
    topic: str
    platform: str = "Instagram"
    slides_count: int = 5
    language: str = "pt-BR"
    tone: str = "inspiracional"
    niche: str = ""


class ImageGenRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    style: str = "moderno"


class SlideData(BaseModel):
    title: str
    body: str
    cta: Optional[str] = None
    bg_color: str = "#1a1a2e"
    text_color: str = "#ffffff"
    accent_color: str = "#e94560"
    image_b64: Optional[str] = None
    font_style: str = "bold"
    layout: str = "center"


class ExportRequest(BaseModel):
    slides: list[SlideData]
    username: Optional[str] = None
    width: int = 1080
    height: int = 1080


# ─── Health (testado pelo Railway) ─────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "google_api_key_set": bool(GOOGLE_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "imagen_model": IMAGEN_MODEL,
    }


# ─── Geração de texto via Gemini ───────────────────────────────────────────────

@app.post("/api/generate-text")
async def generate_carousel_text(req: TextGenRequest):
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY não configurada.")

    niche_part = f" no nicho de {req.niche}" if req.niche else ""

    prompt = f"""Você é um especialista em conteúdo viral para {req.platform}{niche_part}.
Tom: {req.tone}. Idioma: {req.language}.

Crie um carrossel de {req.slides_count} slides sobre: "{req.topic}"

Retorne SOMENTE JSON válido (sem markdown, sem ```), no formato:
{{
  "title": "Título do carrossel",
  "hashtags": ["tag1","tag2","tag3","tag4","tag5"],
  "caption": "Legenda pronta para o post (1-2 frases + call to action)",
  "slides": [
    {{
      "slide_number": 1,
      "title": "Título impactante (máx 50 chars)",
      "body": "Texto do corpo (máx 120 chars)",
      "cta": "Call to action (somente no último slide, vazio nos demais)",
      "suggested_image_prompt": "Descrição em inglês para gerar imagem AI"
    }}
  ]
}}

Regras:
- Slide 1: capa com gancho forte
- Slides 2 a {req.slides_count - 1}: conteúdo de valor
- Slide {req.slides_count}: CTA claro
- suggested_image_prompt em inglês, descritivo, estilo cinemático"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, json=payload)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=f"Erro Gemini: {res.text[:400]}")

    data = res.json()
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao parsear JSON: {e}")


# ─── Geração de imagem via Imagen 3 ────────────────────────────────────────────

@app.post("/api/generate-image")
async def gen_image(req: ImageGenRequest):
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY não configurada.")

    style_map = {
        "moderno":     "modern, clean, professional, high quality",
        "artistico":   "artistic, painterly, colorful, expressive",
        "minimalista": "minimalist, white space, elegant, simple",
        "vibrante":    "vibrant, bold colors, energetic, dynamic",
        "escuro":      "dark aesthetic, moody, cinematic, dramatic lighting",
        "gradiente":   "gradient background, neon colors, digital art",
    }
    style_sfx = style_map.get(req.style, "high quality, professional")
    full_prompt = f"{req.prompt}, {style_sfx}, no text, no watermark, no letters"

    ar_map = {"1:1": "1:1", "9:16": "9:16", "16:9": "16:9", "3:4": "3:4", "4:3": "4:3"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGEN_MODEL}:predict?key={GOOGLE_API_KEY}"
    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": ar_map.get(req.aspect_ratio, "1:1"),
            "safetyFilterLevel": "block_few",
            "personGeneration": "allow_adult",
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.post(url, json=payload)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail=f"Erro Imagen: {res.text[:400]}")

    data = res.json()
    try:
        b64 = data["predictions"][0]["bytesBase64Encoded"]
        return {"image": f"data:image/png;base64,{b64}", "prompt_used": full_prompt}
    except (KeyError, IndexError) as e:
        raise HTTPException(status_code=500, detail=f"Resposta inesperada do Imagen: {e}")


# ─── Exportação ZIP de JPGs via Pillow ─────────────────────────────────────────

@app.post("/api/export-zip")
async def export_zip(req: ExportRequest):
    try:
        from PIL import Image, ImageDraw, ImageFont
        import textwrap
    except ImportError:
        raise HTTPException(status_code=500, detail="Pillow não instalado")

    def hex_rgb(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def rrect(draw, xy, r, fill):
        x1, y1, x2, y2 = xy
        draw.rectangle([x1+r, y1, x2-r, y2], fill=fill)
        draw.rectangle([x1, y1+r, x2, y2-r], fill=fill)
        for cx, cy in [(x1,y1),(x2-2*r,y1),(x1,y2-2*r),(x2-2*r,y2-2*r)]:
            draw.ellipse([cx, cy, cx+2*r, cy+2*r], fill=fill)

    BOLD_FONTS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    BODY_FONTS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    def get_font(paths, size):
        for p in paths:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    W, H = req.width, req.height
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, slide in enumerate(req.slides):
            img = Image.new("RGB", (W, H), color=hex_rgb(slide.bg_color))
            draw = ImageDraw.Draw(img)

            if slide.image_b64:
                try:
                    b64d = slide.image_b64
                    if "," in b64d:
                        b64d = b64d.split(",", 1)[1]
                    bg = Image.open(io.BytesIO(base64.b64decode(b64d))).convert("RGB").resize((W, H))
                    ov = Image.new("RGBA", (W, H), (0, 0, 0, 160))
                    img = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass

            acc = hex_rgb(slide.accent_color)
            txt = hex_rgb(slide.text_color)
            pad = int(W * 0.08)

            draw.rectangle([0, 0, W, 8], fill=acc)
            draw.rectangle([0, H-8, W, H], fill=acc)

            title_font = get_font(BOLD_FONTS, int(W * 0.065))
            body_font  = get_font(BODY_FONTS, int(W * 0.038))
            small_font = get_font(BODY_FONTS, int(W * 0.028))

            # Número e usuário
            draw.text((W-90, 20), f"{idx+1}/{len(req.slides)}", font=small_font, fill=acc)
            if req.username:
                draw.text((30, 20), f"@{req.username}", font=small_font, fill=txt)

            import textwrap as tw

            if slide.layout == "center":
                lines = tw.wrap(slide.title, width=22)
                lh = int(W * 0.082)
                tot = len(lines) * lh
                ty = H//2 - tot//2 - int(H*0.08)
                for line in lines:
                    bb = draw.textbbox((0,0), line, font=title_font)
                    draw.text(((W-(bb[2]-bb[0]))//2, ty), line, font=title_font, fill=txt)
                    ty += lh
                draw.rectangle([(W//2-60, ty+12),(W//2+60, ty+16)], fill=acc)
                if slide.body:
                    by = ty + 32
                    for line in tw.wrap(slide.body, width=38):
                        bb = draw.textbbox((0,0), line, font=body_font)
                        draw.text(((W-(bb[2]-bb[0]))//2, by), line, font=body_font, fill=txt)
                        by += int(W*0.048)
                if slide.cta:
                    ch, cy2 = int(H*0.10), H-int(H*0.18)
                    rrect(draw, (pad, cy2, W-pad, cy2+ch), 18, acc)
                    bb = draw.textbbox((0,0), slide.cta, font=body_font)
                    lw2 = bb[2]-bb[0]
                    draw.text(((W-lw2)//2, cy2+(ch-(bb[3]-bb[1]))//2), slide.cta, font=body_font, fill=(255,255,255))
            else:
                yp = int(H*0.2)
                for line in tw.wrap(slide.title, width=20):
                    draw.text((pad, yp), line, font=title_font, fill=txt)
                    yp += int(W*0.082)
                draw.rectangle([(pad, yp+8),(pad+80, yp+12)], fill=acc)
                yp += int(W*0.07)
                if slide.body:
                    for line in tw.wrap(slide.body, width=32):
                        draw.text((pad, yp), line, font=body_font, fill=txt)
                        yp += int(W*0.052)
                if slide.cta:
                    ch2, cy3 = int(H*0.09), H-int(H*0.16)
                    rrect(draw, (pad, cy3, W-pad, cy3+ch2), 14, acc)
                    draw.text((pad+24, cy3+(ch2-int(W*0.038))//2), slide.cta, font=body_font, fill=(255,255,255))

            jbuf = io.BytesIO()
            img.save(jbuf, "JPEG", quality=95, optimize=True)
            jbuf.seek(0)
            zf.writestr(f"slide_{idx+1:02d}.jpg", jbuf.getvalue())

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=carrossel.zip"},
    )


# ─── Serve o frontend estático — DEVE ser o último ─────────────────────────────
# O frontend fica em ./frontend/index.html relativo a este arquivo (raiz do projeto)
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
