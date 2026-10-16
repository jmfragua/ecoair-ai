#!/bin/sh
# Genera la configuracion de Mosquitto al arrancar, desde variables de entorno.
# Las claves NUNCA se escriben en el repo ni en el log: solo en el archivo de claves del
# contenedor, que mosquitto_passwd convierte a hash al momento.
#
# Modos:
#  1) Autenticacion (recomendado; obligatorio en Railway): MQTT_DEVICE_USER/PASSWORD y
#     MQTT_INGESTOR_USER/PASSWORD. Anonimos rechazados. ACL:
#       - dispositivo: solo PUBLICA en ecoair/+/telemetry
#       - ingestor:    solo SE SUSCRIBE a ecoair/+/telemetry
#  2) Anonimo (solo compatibilidad local): sin esas variables y MQTT_ALLOW_ANONYMOUS=true.
#  3) Sin credenciales y sin MQTT_ALLOW_ANONYMOUS=true: no arranca (error claro).
set -eu

CONF_DIR=/mosquitto/config
CONF="$CONF_DIR/mosquitto.conf"
PASSWD="$CONF_DIR/passwd"
ACL="$CONF_DIR/acl"
PORT="${MQTT_LISTEN_PORT:-1883}"
TOPIC="ecoair/+/telemetry"

error() { echo "ERROR (mosquitto): $*" >&2; exit 1; }

usuario_valido() {
    case "$1" in
        ""|*[!A-Za-z0-9_.-]*) return 1 ;;
        *) return 0 ;;
    esac
}

CREDS="${MQTT_DEVICE_USER:-}${MQTT_DEVICE_PASSWORD:-}${MQTT_INGESTOR_USER:-}${MQTT_INGESTOR_PASSWORD:-}"

if [ -n "$CREDS" ]; then
    for v in MQTT_DEVICE_USER MQTT_DEVICE_PASSWORD MQTT_INGESTOR_USER MQTT_INGESTOR_PASSWORD; do
        eval "valor=\${$v:-}"
        [ -n "$valor" ] || error "falta $v (las cuatro variables MQTT_DEVICE_* y MQTT_INGESTOR_* van juntas)"
    done
    usuario_valido "$MQTT_DEVICE_USER" || error "MQTT_DEVICE_USER solo admite letras, numeros, '.', '_' y '-'"
    usuario_valido "$MQTT_INGESTOR_USER" || error "MQTT_INGESTOR_USER solo admite letras, numeros, '.', '_' y '-'"
    [ "$MQTT_DEVICE_USER" != "$MQTT_INGESTOR_USER" ] || error "el usuario del dispositivo y el del ingestor deben ser distintos"

    umask 077
    # usuario:clave en texto plano -> mosquitto_passwd -U lo reemplaza por hashes (sin pasar
    # la clave como argumento de un proceso).
    printf '%s:%s\n%s:%s\n' "$MQTT_DEVICE_USER" "$MQTT_DEVICE_PASSWORD" \
        "$MQTT_INGESTOR_USER" "$MQTT_INGESTOR_PASSWORD" > "$PASSWD"
    mosquitto_passwd -U "$PASSWD"

    cat > "$ACL" <<EOF
# Generado al arrancar. Sin reglas generales: lo que no se permite, se niega.
user $MQTT_DEVICE_USER
topic write $TOPIC

user $MQTT_INGESTOR_USER
topic read $TOPIC
EOF

    cat > "$CONF" <<EOF
listener $PORT
allow_anonymous false
password_file $PASSWD
acl_file $ACL
persistence false
log_dest stdout
log_type error
log_type warning
log_type notice
EOF
    chown mosquitto:mosquitto "$PASSWD" "$ACL" "$CONF"
    chmod 0600 "$PASSWD" "$ACL"
    echo "Mosquitto: autenticacion activa en el puerto $PORT (dispositivo '$MQTT_DEVICE_USER' solo publica, ingestor '$MQTT_INGESTOR_USER' solo se suscribe; anonimos rechazados)"

elif [ "${MQTT_ALLOW_ANONYMOUS:-false}" = "true" ]; then
    cat > "$CONF" <<EOF
listener $PORT
allow_anonymous true
persistence false
log_dest stdout
EOF
    chown mosquitto:mosquitto "$CONF"
    echo "AVISO (mosquitto): modo ANONIMO sin autenticacion (MQTT_ALLOW_ANONYMOUS=true). Solo para uso local." >&2

else
    error "faltan MQTT_DEVICE_USER/PASSWORD y MQTT_INGESTOR_USER/PASSWORD (o MQTT_ALLOW_ANONYMOUS=true solo en local)"
fi

exec mosquitto -c "$CONF"
