# --- IMPORTS ---
# AGREGADO: send_from_directory para poder enviar el HTML y las imágenes
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import datetime
import re
import concurrent.futures
import os   # <--- NECESARIO PARA LA NUBE
import json # <--- NECESARIO PARA LA NUBE

# --- CONFIGURACIÓN ---
app = Flask(__name__)
CORS(app)

# --- CONEXIÓN FIREBASE HÍBRIDA (LOCAL Y NUBE) ---
if not firebase_admin._apps:
    if os.path.exists("serviceAccountKey.json"):
        # Modo Local (Tu PC)
        cred = credentials.Certificate("serviceAccountKey.json")
    else:
        # Modo Nube (Render) - Lee la variable oculta
        key_content = os.environ.get('FIREBASE_CREDENTIALS')
        if key_content:
            key_dict = json.loads(key_content)
            cred = credentials.Certificate(key_dict)
        else:
            print("⚠️ ADVERTENCIA: No se encontró serviceAccountKey.json ni la variable FIREBASE_CREDENTIALS")
            cred = None

    if cred:
        firebase_admin.initialize_app(cred)

db = firestore.client()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'es-CL,es;q=0.9',
    'Connection': 'keep-alive'
}

# --- HERRAMIENTAS ---
def arreglar_img(url):
    if not url: return "https://via.placeholder.com/150"
    if url.startswith("//"): return "https:" + url
    return url

def validar_titulo(titulo_producto, termino_busqueda):
    t = titulo_producto.lower()
    palabras = termino_busqueda.lower().split()
    for palabra in palabras:
        if palabra not in t:
            return False
    return True

def generar_variaciones(busqueda):
    variaciones = [busqueda]
    separado = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', busqueda)
    if separado != busqueda: variaciones.append(separado)
    return variaciones

# --- ROBOT 1: API SHOPIFY ---
def buscar_api(nombre_tienda, url_base, busqueda_original):
    session = requests.Session()
    intentos = generar_variaciones(busqueda_original)
    candidatos = [] 

    for termino in intentos:
        try:
            url_api = f"{url_base}/search/suggest.json"
            resp = session.get(url_api, params={"q": termino, "resources[type]": "product"}, headers=HEADERS, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                productos = data.get("resources", {}).get("results", {}).get("products", [])
                for p in productos:
                    if validar_titulo(p['title'], busqueda_original):
                        precio = int(''.join(filter(str.isdigit, str(p.get('price', '0')))))
                        if precio > 1000:
                            candidatos.append({
                                "nombre": nombre_tienda, 
                                "nombre_detectado": p['title'],
                                "precio": precio, 
                                "url": url_base + p['url'], 
                                "imagen": arreglar_img(p['image'])
                            })
        except: pass
    
    if candidatos:
        candidatos.sort(key=lambda x: len(x['nombre_detectado']))
        return candidatos[0] 
    return None

# --- ROBOT 2: HTML GENÉRICO (MEJORADO ANTI-WEBPAY) ---
def buscar_html(nombre, url_base, busqueda_original):
    session = requests.Session()
    intentos = generar_variaciones(busqueda_original)
    candidatos = [] 

    for termino in intentos:
        termino_url = termino.replace(" ", "+")
        if "lodoro" in url_base: url = f"{url_base}/search?q={termino_url}&options%5Bprefix%5D=last"
        elif "joyperfumes" in url_base: url = f"{url_base}/search?q={termino_url}&type=product"
        elif "cosmetic" in url_base: url = f"{url_base}/search/{termino_url}"
        else: url = f"{url_base}/search?q={termino_url}"

        try:
            resp = session.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                enlaces = soup.find_all('a', href=True)
                for link in enlaces:
                    href = link['href']
                    if '/products/' in href or '/producto/' in href:
                        titulo = link.get_text().strip()
                        if not titulo:
                            padre = link.find_parent('div')
                            if padre:
                                h = padre.find(['h3', 'h4', 'div'], class_=re.compile('title|name'))
                                if h: titulo = h.get_text().strip()
                        
                        if not titulo or not validar_titulo(titulo, busqueda_original): continue
                        
                        padre = link.find_parent('div') or link.find_parent('li')
                        if padre:
                            precios = re.findall(r'\$\s?([\d\.]+)', padre.get_text().replace(',', ''))
                            if precios:
                                precio = int(precios[0].replace('.', ''))
                                if precio > 2000:
                                    # --- CORRECCIÓN DE IMAGEN ---
                                    imagenes_encontradas = padre.find_all('img')
                                    src_img = ""
                                    for img in imagenes_encontradas:
                                        posible_src = img.get('data-src') or img.get('src') or ""
                                        if "webpay" in posible_src.lower() or "transbank" in posible_src.lower() or "icon" in posible_src.lower():
                                            continue
                                        if posible_src:
                                            src_img = posible_src
                                            break 
                                    
                                    candidatos.append({
                                        "nombre": nombre, 
                                        "nombre_detectado": titulo,
                                        "precio": precio, 
                                        "url": url_base + href if not href.startswith("http") else href,
                                        "imagen": arreglar_img(src_img)
                                    })
        except: pass
    
    if candidatos:
        candidatos.sort(key=lambda x: len(x['nombre_detectado']))
        return candidatos[0]
        
    return None

# --- ROBOT 3: MERCADO LIBRE ---
def buscar_mercadolibre(busqueda_original):
    session = requests.Session()
    termino = busqueda_original.replace(" ", "-")
    url = f"https://listado.mercadolibre.cl/belleza-cuidado-personal/perfumes/{termino}_NoIndex_True"
    candidatos = []

    try:
        resp = session.get(url, headers=HEADERS, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.find_all('li', class_='ui-search-layout__item')
            
            for item in items:
                titulo_tag = item.find('h2', class_='ui-search-item__title') or item.find('a', class_='ui-search-item__group__element')
                if not titulo_tag: continue
                titulo = titulo_tag.get_text().strip()

                if validar_titulo(titulo, busqueda_original):
                    link_tag = item.find('a', class_='ui-search-link')
                    url_prod = link_tag['href'] if link_tag else "#"
                    
                    price_container = item.find('div', class_='ui-search-price__second-line') or item.find('div', class_='ui-search-price__part-without-link')
                    if price_container:
                        fraction = price_container.find('span', class_='andes-money-amount__fraction')
                        if fraction:
                            precio = int(fraction.get_text().replace('.', ''))
                            img_tag = item.find('img')
                            src_img = img_tag.get('data-src') or img_tag.get('src') if img_tag else ""

                            candidatos.append({
                                "nombre": "Mercado Libre", "nombre_detectado": titulo,
                                "precio": precio, "url": url_prod, "imagen": arreglar_img(src_img)
                            })
    except: pass
    
    if candidatos:
        candidatos.sort(key=lambda x: len(x['nombre_detectado']))
        return candidatos[0]
    return None

# --- RUTA API ---
@app.route('/api/cotizar', methods=['GET'])
def cotizar_endpoint():
    termino = request.args.get('q')
    if not termino: return jsonify({"error": "Falta termino"}), 400

    print(f"\n⚡ BUSCANDO (NUBE READY): '{termino}'")
    producto_id = termino.lower().replace(" ", "-")
    
    misiones = [
        lambda: buscar_api("ElitePerfumes", "https://eliteperfumes.cl", termino),
        lambda: buscar_api("OfertaPerfumes", "https://ofertaperfumes.cl", termino),
        lambda: buscar_html("Lodoro", "https://lodoro.cl", termino),
        lambda: buscar_html("JoyPerfumes", "https://joyperfumes.cl", termino),
        lambda: buscar_html("SilkPerfumes", "https://silkperfumes.cl", termino),
        lambda: buscar_html("AlishaPerfumes", "https://alishaperfumes.cl", termino),
        lambda: buscar_html("Cosmetic", "https://cosmetic.cl", termino),
        lambda: buscar_html("Yauras", "https://yauras.cl", termino),
        lambda: buscar_mercadolibre(termino)
    ]
    
    resultados = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futuros = [executor.submit(m) for m in misiones]
        for f in concurrent.futures.as_completed(futuros):
            try:
                res = f.result()
                if res: resultados.append(res)
            except: pass

    if resultados:
        resultados.sort(key=lambda x: x['precio'])
        
        mejor_img = "https://via.placeholder.com/150"
        for r in resultados:
            if "via.placeholder" not in r['imagen'] and "http" in r['imagen']:
                mejor_img = r['imagen']
                break

        if len(resultados) < 2:
            p_ref = int(resultados[0]['precio'] * 1.35)
            resultados.append({"nombre": "Falabella (Ref)", "nombre_detectado": f"Ref: {termino}", "precio": p_ref, "url": "#", "imagen": ""})

        db.collection("perfumes").document(producto_id).set({
            "nombre": termino.title(),
            "marca": "Cotizador Multi-Tienda",
            "imagen": mejor_img,
            "descripcion": f"Mejor precio: ${resultados[0]['precio']:,}".replace(",", "."),
            "fechaActualizacion": datetime.datetime.now(),
            "tiendas": resultados
        }, merge=True)
        
        return jsonify({"status": "success", "data": resultados})
    else:
        return jsonify({"status": "error"}), 404

# --- RUTAS NUEVAS PARA QUE SE VEA LA PÁGINA (FRONTEND) ---
@app.route('/')
def index():
    # Cuando entres a la raiz, devuelve el index.html
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    # Cuando el html pida "css/estilos.css" o "img/fondo.jpg", entrégalo
    return send_from_directory('.', path)

if __name__ == '__main__':
    # '0.0.0.0' permite conexión externa
    app.run(debug=True, port=5000, host='0.0.0.0')