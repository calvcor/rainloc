FROM nginx:alpine

# Configuración personalizada de Nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copiar archivos del Frontend
COPY index.html /usr/share/nginx/html/
COPY styles.css /usr/share/nginx/html/
COPY package.json /usr/share/nginx/html/
COPY manifest.json /usr/share/nginx/html/
COPY manifest.webmanifest /usr/share/nginx/html/
COPY sw.js /usr/share/nginx/html/
COPY favicon.png /usr/share/nginx/html/
COPY favicon.jpeg /usr/share/nginx/html/
COPY favicon.ico /usr/share/nginx/html/
COPY apple-touch-icon.png /usr/share/nginx/html/
COPY apple-touch-icon-precomposed.png /usr/share/nginx/html/
COPY js/ /usr/share/nginx/html/js/
COPY icons/ /usr/share/nginx/html/icons/
COPY subsistemas.optimized.geojson /usr/share/nginx/html/
COPY subsistemas.geojson /usr/share/nginx/html/
COPY ccaa.geojson /usr/share/nginx/html/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
