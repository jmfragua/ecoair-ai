# Mosquitto con usuario/clave y ACL generados al arrancar desde variables de entorno
# (ver entrypoint.sh). Ninguna clave se guarda en el repo ni en la imagen.
FROM eclipse-mosquitto:2

COPY entrypoint.sh /ecoair-entrypoint.sh
RUN chmod 0755 /ecoair-entrypoint.sh

EXPOSE 1883
ENTRYPOINT ["/ecoair-entrypoint.sh"]
