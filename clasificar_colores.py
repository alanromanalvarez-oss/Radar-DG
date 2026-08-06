"""Clasifica cada SKU en una familia de color a partir de su miniatura."""
import json, base64, io, colorsys
import numpy as np
from PIL import Image

CAT="/sessions/kind-confident-turing/mnt/outputs/radar_dg_app/catalog_index.json"

def recorte(im, margen=0.04, umbral=28, franja=0.03):
    arr=np.array(im.convert("RGB")).astype(np.int16); h,w=arr.shape[:2]
    fb=max(2,int(min(h,w)*franja))
    borde=np.concatenate([arr[:fb,:,:].reshape(-1,3),arr[-fb:,:,:].reshape(-1,3),
                          arr[:,:fb,:].reshape(-1,3),arr[:,-fb:,:].reshape(-1,3)])
    fondo=np.median(borde,axis=0)
    d=np.sqrt(((arr-fondo)**2).sum(axis=2))
    m=d>umbral
    ys,xs=np.where(m)
    if len(xs)==0: return im.convert("RGB"), None
    x0,y0,x1,y1=xs.min(),ys.min(),xs.max(),ys.max()
    return im.convert("RGB").crop((x0,y0,x1,y1)), (fondo, umbral)

def familia(h,s,v):
    if v<0.20: return "Negro"
    if s<0.12:
        if v>0.80: return "Blanco"
        return "Gris"
    hd=h*360
    if s<0.35 and v>0.55 and 15<=hd<60: return "Beige"
    if v<0.50 and 10<=hd<50: return "Café"
    if hd<12 or hd>=340:
        return "Rosa" if (v>0.70 and s<0.50) else "Rojo"
    if 12<=hd<40:  return "Café" if v<0.62 else "Naranja"
    if 40<=hd<68:  return "Amarillo"
    if 68<=hd<168: return "Verde"
    if 168<=hd<258:return "Azul"
    if 258<=hd<295:return "Morado"
    return "Rosa"

def color_de(thumb_b64):
    im=Image.open(io.BytesIO(base64.b64decode(thumb_b64)))
    rec,info=recorte(im)
    rec.thumbnail((90,90))
    arr=np.array(rec.convert("RGB")).astype(np.float32)/255.0
    px=arr.reshape(-1,3)
    if info:
        fondo=info[0]/255.0
        d=np.sqrt(((px-fondo)**2).sum(axis=1))
        px=px[d>0.11]
    if len(px)<30: px=arr.reshape(-1,3)
    cnt={}
    for r,g,b in px:
        h,s,v=colorsys.rgb_to_hsv(r,g,b)
        f=familia(h,s,v)
        cnt[f]=cnt.get(f,0)+1
    tot=sum(cnt.values())
    orden=sorted(cnt.items(), key=lambda kv:-kv[1])
    top,n=orden[0]
    frac=n/tot
    if frac<0.38 and len(orden)>1 and orden[1][1]/tot>0.30:
        return "Multicolor", frac, orden[:3]
    return top, frac, orden[:3]

cat=json.load(open(CAT,encoding="utf-8"))
out={}
from collections import Counter
c=Counter()
for r in cat:
    if not r.get("thumb_b64"): continue
    try:
        f,frac,_=color_de(r["thumb_b64"])
    except Exception:
        continue
    out[r["sku"]]=f; c[f]+=1
json.dump(out, open("/tmp/colores.json","w",encoding="utf-8"), ensure_ascii=False)
print("clasificados:",len(out))
for k,v in c.most_common(): print(f"  {k:12s} {v}")
