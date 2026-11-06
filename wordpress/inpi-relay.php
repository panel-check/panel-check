<?php
/**
 * Reenvío de pedidos al INPI desde el hosting de la web (en Argentina).
 *
 * Desde el 06/11/2026 el portal del INPI no acepta conexiones desde servidores fuera de Argentina
 * (Railway, GitHub Actions). El panel y los procesos le mandan acá cada pedido al INPI y este archivo
 * lo hace desde este servidor y devuelve la respuesta tal cual (estado, tipo, cookies y contenido).
 *
 * Instalación:
 *   1. Cambiar CLAVE_RELAY (abajo) por una clave larga y al azar.
 *   2. Subir el archivo a la raíz del sitio (por ejemplo https://smartiesconsultora.com.ar/inpi-relay.php).
 *   3. Cargar la misma URL y la misma clave en Railway y en los secrets de GitHub:
 *        INPI_RELAY_URL   = https://smartiesconsultora.com.ar/inpi-relay.php
 *        INPI_RELAY_CLAVE = la clave
 *   4. Probar: abrir  https://smartiesconsultora.com.ar/inpi-relay.php?prueba=1&clave=LA_CLAVE
 *      Tiene que decir "ok": true (el INPI respondió desde este servidor).
 *
 * Seguridad: sin la clave responde 404, y solo reenvía a portaltramites.inpi.gob.ar y ws.inpi.gob.ar
 * por https. No guarda nada. El código del panel que lo usa está en panel/inpi_relay.py.
 */

const CLAVE_RELAY = 'CAMBIAR-POR-UNA-CLAVE-LARGA';
const HOSTS_PERMITIDOS = ['portaltramites.inpi.gob.ar', 'ws.inpi.gob.ar'];
const TIEMPO_MAXIMO = 90; // segundos por pedido

@set_time_limit(TIEMPO_MAXIMO + 10);
@ini_set('zlib.output_compression', '0');

function no_encontrado() {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    exit('Not found');
}

function falla($codigo, $mensaje) {
    http_response_code($codigo);
    header('Content-Type: text/plain; charset=utf-8');
    header('X-Relay-Error: ' . preg_replace('/[\r\n]+/', ' ', $mensaje));
    exit($mensaje);
}

$clave = $_SERVER['HTTP_X_RELAY_CLAVE'] ?? ($_GET['clave'] ?? '');
if (CLAVE_RELAY === 'CAMBIAR-POR-UNA-CLAVE-LARGA' || !is_string($clave) || !hash_equals(CLAVE_RELAY, $clave)) {
    no_encontrado();
}
if (!function_exists('curl_init')) {
    falla(500, 'El hosting no tiene la extension curl de PHP.');
}

// ── Prueba: ¿el INPI responde desde este servidor? ─────────────────────────
if (isset($_GET['prueba'])) {
    $inicio = microtime(true);
    $ch = curl_init('https://portaltramites.inpi.gob.ar/marcasconsultas/busqueda/?Cod_Funcion=NQA0ADE');
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 30, CURLOPT_CONNECTTIMEOUT => 15,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (relay smarties)']);
    $cuerpo = curl_exec($ch);
    $estado = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode([
        'ok' => $estado === 200 && is_string($cuerpo) && stripos($cuerpo, 'BUSCADOR DE MARCAS') !== false,
        'estado_http' => $estado,
        'error' => $error ?: null,
        'segundos' => round(microtime(true) - $inicio, 2),
        'ip_del_servidor' => $_SERVER['SERVER_ADDR'] ?? null,
        'php' => PHP_VERSION,
    ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
    exit;
}

// ── Reenvío ────────────────────────────────────────────────────────────────
// La dirección del INPI llega codificada en base64url («b»): una URL https:// a la vista dentro de la
// dirección la bloquea el firewall de muchos hostings compartidos (ModSecurity, regla de inclusión remota).
$destino = '';
if (isset($_GET['b']) && is_string($_GET['b'])) {
    $destino = base64_decode(strtr($_GET['b'], '-_', '+/'), true) ?: '';
} elseif (isset($_GET['u'])) {
    $destino = $_GET['u'];
}
$partes = is_string($destino) ? parse_url($destino) : false;
if (!$partes || ($partes['scheme'] ?? '') !== 'https' || !in_array(strtolower($partes['host'] ?? ''), HOSTS_PERMITIDOS, true)
    || isset($partes['user']) || isset($partes['port'])) {
    falla(400, 'Destino no permitido.');
}

$metodo = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
if (!in_array($metodo, ['GET', 'POST', 'HEAD'], true)) {
    falla(405, 'Metodo no permitido.');
}

// Cabeceras que se pasan tal cual al INPI (las demás, como Host o la clave, no).
$pasar = ['CONTENT_TYPE' => 'Content-Type', 'HTTP_SOAPACTION' => 'SOAPAction', 'HTTP_X_REQUESTED_WITH' => 'X-Requested-With',
    'HTTP_REFERER' => 'Referer', 'HTTP_COOKIE' => 'Cookie', 'HTTP_ACCEPT' => 'Accept',
    'HTTP_ACCEPT_LANGUAGE' => 'Accept-Language', 'HTTP_USER_AGENT' => 'User-Agent', 'HTTP_ORIGIN' => 'Origin'];
$cabeceras = [];
foreach ($pasar as $clave_srv => $nombre) {
    if (isset($_SERVER[$clave_srv]) && $_SERVER[$clave_srv] !== '') {
        $cabeceras[] = $nombre . ': ' . $_SERVER[$clave_srv];
    }
}
$cabeceras[] = 'Expect:';

$ch = curl_init($destino);
curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $metodo,
    CURLOPT_NOBODY => $metodo === 'HEAD',
    CURLOPT_HTTPHEADER => $cabeceras,
    CURLOPT_FOLLOWLOCATION => false,        // las redirecciones las sigue el panel
    CURLOPT_CONNECTTIMEOUT => 15,
    CURLOPT_TIMEOUT => TIEMPO_MAXIMO,
    CURLOPT_ENCODING => '',                 // acepta gzip y lo devuelve ya descomprimido
    CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
]);
if ($metodo === 'POST') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}

// Se devuelve a medida que llega (los PDF de los boletines pueden ser grandes).
$encabezado_enviado = false;
curl_setopt($ch, CURLOPT_HEADERFUNCTION, function ($ch, $linea) use (&$encabezado_enviado) {
    $largo = strlen($linea);
    $limpia = trim($linea);
    if (preg_match('#^HTTP/\S+\s+(\d{3})#', $limpia, $m)) {
        http_response_code((int)$m[1]);
        return $largo;
    }
    if ($limpia === '' || strpos($limpia, ':') === false) {
        return $largo;
    }
    [$nombre, $valor] = array_map('trim', explode(':', $limpia, 2));
    $n = strtolower($nombre);
    if (in_array($n, ['content-type', 'location', 'content-disposition', 'cache-control'], true)) {
        header($nombre . ': ' . $valor, true);
    } elseif ($n === 'set-cookie') {
        header('Set-Cookie: ' . $valor, false);
    }
    return $largo;
});
curl_setopt($ch, CURLOPT_WRITEFUNCTION, function ($ch, $datos) use (&$encabezado_enviado) {
    $encabezado_enviado = true;
    echo $datos;
    @flush();
    return strlen($datos);
});

$ok = curl_exec($ch);
$error = curl_error($ch);
curl_close($ch);
if ($ok === false && !$encabezado_enviado) {
    falla(502, 'El INPI no respondio desde el hosting: ' . $error);
}
