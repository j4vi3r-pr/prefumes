import firebase_admin
from firebase_admin import credentials, firestore
import requests
from bs4 import BeautifulSoup
import datetime
import re
import time

# --- 1. CONEXIÓN ---
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
db = firestore.client()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'es-CL,es;q=0.9'
}

# --- HERRAMIENTAS ---
def limpiar(texto):
    """Quita todo lo que no sea letra o numero para comparar"""
    return texto.lower().replace(" ", "").replace(".", "").replace("-", "")

def validar_titulo(titulo_producto, termino_busqueda):
    """
    El Juez: Verifica si lo encontrado se parece a lo buscado.
    """
    t = limpiar(titulo_producto)
    b = limpiar(termino_busqueda)
    return b in t

def generar_variaciones(busqueda):
    """
    Genera variaciones inteligentes para probar en el buscador.
    Si buscas '9am', genera ['9am', '9 am'].
    """
    variaciones = [busqueda]
    # Intenta separar numeros de letras (9am -> 9 am)
    separado = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', busqueda)
    if separado != busqueda:
        variaciones.append(separado)
    return variaciones

def arreglar_img(url):
    if not url: return "https://via.placeholder.com/150"
    if url.startswith("//"): return "https:" + url
    return url

# ==========================================
#  ROBOT 1: API SHOPIFY (Elite, Oferta)
# ==========================================
def buscar_api(nombre_tienda, url_base, busqueda_original):
    # Probamos con variaciones (Doble Disparo)
    intentos = generar_variaciones(busqueda_original)
    
    print(f"   🔹 {nombre_tienda}: Probando con {intentos}...")
    
    for termino in intentos:
        try:
            url_api = f"{url_base}/search/suggest.json"
            resp = requests.get(url_api, params={"q": termino, "resources[type]": "product"}, headers=HEADERS, timeout=4)
            
            if resp.status_code == 200:
                data = resp.json()
                productos = data.get("resources", {}).get("results", {}).get("products", [])
                
                for p in productos:
                    # Validamos contra la búsqueda ORIGINAL (sin modificar)
                    if validar_titulo(p['title'], busqueda_original):
                        precio_str = str(p.get('price', '0'))
                        precio = int(''.join(filter(str.isdigit, precio_str)))
                        if precio > 1000:
                            return {
                                "nombre": nombre_tienda,
                                "nombre_detectado": p['title'],
                                "precio": precio,
                                "url": url_base + p['url'],
                                "imagen": arreglar_img(p['image'])
                            }
        except: pass
    
    return None

# ==========================================
#  ROBOT 2: HTML VISUAL (Lodoro, Joy, Silk)
# ==========================================
def buscar_html(nombre, url_base, busqueda_original, es_lodoro=False):
    intentos = generar_variaciones(busqueda_original)
    print(f"   🔸 {nombre}: Probando con {intentos}...")

    for termino in intentos:
        termino_url = termino.replace(" ", "+")
        
        # Construcción de URL específica para cada tienda
        if "lodoro" in url_base:
            url_final = f"{url_base}/search?q={termino_url}&options%5Bprefix%5D=last"
        elif "joyperfumes" in url_base:
            url_final = f"{url_base}/search?q={termino_url}&type=product"
        else: # Silk y genéricos
            url_final = f"{url_base}/search?q={termino_url}"

        try:
            resp = requests.get(url_final, headers=HEADERS, timeout=6)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                enlaces = soup.find_all('a', href=True)
                
                for link in enlaces:
                    if '/products/' in link['href']:
                        # 1. Título
                        titulo = link.get_text().strip()
                        if not titulo: # Buscar título cerca si el link está vacío
                            padre = link.find_parent('div')
                            if padre:
                                h_tag = padre.find(['h3', 'h4', 'div'], class_=re.compile('title|name'))
                                if h_tag: titulo = h_tag.get_text().strip()
                        
                        # 2. Validación
                        if not titulo or not validar_titulo(titulo, busqueda_original):
                            continue # Si no coincide, siguiente producto

                        # 3. Precio
                        padre = link.find_parent('div') or link.find_parent('li')
                        if padre:
                            txt = padre.get_text()
                            precios = re.findall(r'\$\s?([\d\.]+)', txt.replace(',', ''))
                            if precios:
                                precio = int(precios[0].replace('.', ''))
                                if precio > 2000:
                                    # 4. Imagen
                                    img_tag = padre.find('img')
                                    src_img = ""
                                    if img_tag:
                                        src_img = img_tag.get('data-src') or img_tag.get('src')
                                    
                                    return {
                                        "nombre": nombre,
                                        "nombre_detectado": titulo,
                                        "precio": precio,
                                        "url": url_base + link['href'] if not link['href'].startswith("http") else link['href'],
                                        "imagen": arreglar_img(src_img)
                                    }
            # Si termina el loop de enlaces y no encontró nada, prueba la siguiente variación de búsqueda
        except: pass
        
    return None

# ==========================================
#  CEREBRO
# ==========================================
def cotizar():
    entrada = input("\n🔎 Perfume (Ej: 9am, Asad, Cloud): ").strip()
    id_doc = entrada.lower().replace(" ", "-")
    
    print(f"\n⚡ Iniciando escaneo DOBLE para: '{entrada}'")
    
    resultados = []
    
    # Lista de misiones
    # Usamos lambdas para pasar los argumentos correctamente
    misiones = [
        lambda: buscar_api("ElitePerfumes", "https://eliteperfumes.cl", entrada),
        lambda: buscar_api("OfertaPerfumes", "https://ofertaperfumes.cl", entrada),
        lambda: buscar_html("Lodoro", "https://lodoro.cl", entrada),
        lambda: buscar_html("JoyPerfumes", "https://joyperfumes.cl", entrada),
        lambda: buscar_html("SilkPerfumes", "https://silkperfumes.cl", entrada)
    ]
    
    for mision in misiones:
        res = mision()
        if res: 
            print(f"   ✅ {res['nombre']} encontró: {res['nombre_detectado']}")
            resultados.append(res)
        else:
            # Opcional: imprimir quién falló
            # print("   ❌ Tienda X no encontró nada.")
            pass

    # Procesar final
    if resultados:
        resultados.sort(key=lambda x: x['precio'])
        
        # Mejor imagen (que no sea placeholder)
        mejor_img = "https://via.placeholder.com/150"
        for r in resultados:
            if "via.placeholder" not in r['imagen']:
                mejor_img = r['imagen']
                break

        print(f"\n📊 Resumen: {len(resultados)} tiendas encontradas.")
        for r in resultados:
            print(f"   💰 {r['nombre']}: ${r['precio']:,}".replace(",", "."))

        # Relleno Falabella
        if len(resultados) < 2:
            print("   ℹ️ Agregando referencia Falabella...")
            precio_ref = int(resultados[0]['precio'] * 1.35)
            resultados.append({
                "nombre": "Falabella (Ref)", "nombre_detectado": f"Ref: {entrada}",
                "precio": precio_ref, "url": "#", "imagen": ""
            })

        print("\n💾 Subiendo a Firebase...")
        db.collection("perfumes").document(id_doc).set({
            "nombre": entrada.title(),
            "marca": "Cotización",
            "imagen": mejor_img,
            "descripcion": f"Mejor precio: ${resultados[0]['precio']:,}".replace(",", "."),
            "fechaActualizacion": datetime.datetime.now(),
            "tiendas": resultados
        }, merge=True)
        print("🚀 ¡Listo! Recarga tu web.")

    else:
        print("❌ Ninguna tienda encontró el producto.")

cotizar()