<?php
/**
 * Índice de preguntas frecuentes — smartiesconsultora.com.ar/preguntas-frecuentes/
 *
 * Arma SOLO la página con todas las preguntas: título (H1), buscador, una tarjeta por cada entrada de la categoría
 * «Preguntas frecuentes» y el botón al verificador. Se actualiza sola: cada entrada que se publica en esa categoría
 * aparece en la lista, y cada tarjeta lleva al enlace de su entrada.
 *
 * CÓMO PONERLO (WordPress):
 *   1. Plugins → Añadir nuevo → instalar «WPCode» (o «Code Snippets»). No pegar esto en functions.php:
 *      si hay un error, esos plugins desactivan el fragmento solos y el sitio no se cae.
 *   2. Fragmentos de código → Añadir → «Añadir tu código personalizado», tipo «Fragmento PHP», pegar TODO este archivo
 *      SIN la primera línea «<?php» (WPCode ya la pone) → ubicación «Ejecutar en todas partes» → Activar.
 *   3. Entradas → Categorías: crear la categoría «Preguntas frecuentes» con la dirección (slug) preguntas-frecuentes.
 *      Todas las entradas de preguntas van en esa categoría.
 *   4. Páginas → Añadir nueva → título «Preguntas frecuentes sobre registro de marcas» (dirección /preguntas-frecuentes/).
 *      Plantilla «Elementor ancho completo» → un contenedor a ANCHO COMPLETO con margen y relleno en 0 → widget
 *      «Shortcode» (NO «HTML»: ese no ejecuta shortcodes) → escribir:   [preguntas_frecuentes]
 *   5. Plugin de SEO de esa página: título «Preguntas frecuentes sobre registro de marcas en Argentina» y una descripción.
 *   6. Cada vez que se publica una entrada nueva, si el sitio tiene plugin de caché, vaciarla («Vaciar la caché» en la
 *      barra de arriba) para que la lista se actualice enseguida.
 *
 * Opciones del shortcode (todas opcionales):
 *   [preguntas_frecuentes categoria="preguntas-frecuentes" verificador="/consulta-marca-inpi/"]
 *
 * Las tarjetas muestran el título de la entrada (la pregunta) y su descripción: la «meta descripción» de Yoast si la
 * cargaron, y si no, el extracto o el comienzo del texto.
 */

if (!function_exists('smarties_preguntas_frecuentes')) {
    function smarties_preguntas_frecuentes($atts = array()) {
        $a = shortcode_atts(array(
            'categoria'   => 'preguntas-frecuentes',
            'verificador' => '/consulta-marca-inpi/',
        ), $atts, 'preguntas_frecuentes');

        $consulta = new WP_Query(array(
            'post_type'           => 'post',
            'post_status'         => 'publish',
            'category_name'       => sanitize_title($a['categoria']),
            'posts_per_page'      => 200,
            'orderby'             => 'title',
            'order'               => 'ASC',
            'no_found_rows'       => true,
            'ignore_sticky_posts' => true,
        ));

        $items = array();
        while ($consulta->have_posts()) {
            $consulta->the_post();
            $id   = get_the_ID();
            $desc = trim((string) get_post_meta($id, '_yoast_wpseo_metadesc', true));
            if ($desc === '') {
                $desc = wp_strip_all_tags(get_the_excerpt());
            }
            $items[] = array(
                'titulo' => get_the_title(),
                'url'    => get_permalink(),
                'desc'   => wp_trim_words($desc, 28, '…'),
            );
        }
        wp_reset_postdata();

        $lista_ld = array();
        foreach ($items as $i => $it) {
            $lista_ld[] = array('@type' => 'ListItem', 'position' => $i + 1, 'url' => $it['url'], 'name' => $it['titulo']);
        }
        $ld = array('@context' => 'https://schema.org', '@type' => 'ItemList', 'itemListElement' => $lista_ld);

        ob_start();
        ?>
<div id="smf" class="smf">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Mulish:wght@400;600;700&family=Outfit:wght@600;800;900&display=swap">
  <style>
    .smf, .smf * { box-sizing: border-box; }
    .smf { --az:#161250; --ce:#0095ff; --tq:#5cffe1; --gr:#606060; --fo:#f4f7ff;
      font-family: "Mulish","Roboto","Segoe UI",sans-serif; color: var(--gr); font-size: 17px; line-height: 1.6; background: #fff;
      -webkit-font-smoothing: antialiased; width: 100vw; max-width: 100vw; margin-left: calc(50% - 50vw); margin-right: calc(50% - 50vw); overflow: hidden; }
    body:has(#smf) { overflow-x: clip; }
    .smf h1, .smf h2, .smf h3 { font-family: "Outfit","Roboto","Segoe UI",sans-serif; color: var(--az); letter-spacing: 0; margin: 0; }
    .smf a { color: inherit; }
    /* el tema del sitio subraya todos los enlaces: se fuerza sin subrayado en tarjetas y botón (y en lo que llevan adentro) */
    .smf a, .smf a:hover, .smf a:focus, .smf a:visited { text-decoration: none !important; }
    .smf .hero { padding: 150px 24px 70px; text-align: center; background: linear-gradient(160deg,#161250 0%,#2a2580 100%); }
    .smf .hero h1 { max-width: 820px; margin: 0 auto 14px; color: #fff; font-weight: 900; font-size: clamp(30px,5vw,48px); line-height: 1.1; text-transform: uppercase; }
    .smf .hero h1 em { font-style: normal; color: var(--tq); }
    .smf .hero p { max-width: 620px; margin: 0 auto; color: #dfe3ff; font-size: 18px; }
    .smf .buscar { max-width: 640px; margin: -28px auto 0; padding: 0 24px; position: relative; }
    .smf .buscar input { display: block; width: 100%; height: 56px; padding: 0 22px; border: 2px solid var(--tq); border-radius: 30px; background: #fff; color: var(--az);
      font: 400 17px/1 "Mulish","Segoe UI",sans-serif; box-shadow: 0 12px 30px rgba(22,18,80,.15); outline: none; }
    .smf .buscar input:focus { border-color: var(--ce); }
    .smf .caja { max-width: 900px; margin: 0 auto; padding: 36px 24px 8px; }
    .smf .lista { display: grid; gap: 14px; }
    .smf .tarj { display: block; padding: 20px 24px; border: 1px solid #dfe5f5; border-radius: 16px; background: #fff; text-decoration: none; transition: border-color .15s, box-shadow .15s, transform .15s; }
    .smf .tarj:hover { border-color: var(--tq); box-shadow: 0 8px 22px rgba(22,18,80,.1); transform: translateY(-1px); }
    .smf .tarj h2 { font-size: 21px; font-weight: 800; line-height: 1.3; margin-bottom: 6px; }
    .smf .tarj p { margin: 0 0 8px; font-size: 16px; }
    .smf .tarj span { color: var(--ce); font-weight: 700; font-size: 15px; }
    .smf .vacio { padding: 18px 4px; text-align: center; }
    .smf .cta { max-width: 900px; margin: 30px auto 0; padding: 0 24px 60px; }
    .smf .cta > div { background: var(--az); border-radius: 22px; padding: 28px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 16px; }
    .smf .cta h2 { color: #fff; font-size: 24px; margin: 0 0 4px; }
    .smf .cta p { color: #dfe3ff; margin: 0; font-size: 16px; max-width: 460px; } .smf .cta b { color: var(--tq); }
    .smf .btn { display: inline-flex; align-items: center; height: 46px; padding: 0 24px; border-radius: 27px; background: var(--tq); border: 2px solid var(--tq); color: var(--az);
      text-decoration: none; font: 700 16px/1 "Mulish",sans-serif; }
    .smf .btn:hover { background: #fff; border-color: #fff; }
    .smf [hidden] { display: none !important; }
  </style>

  <header class="hero">
    <h1>Preguntas frecuentes sobre <em>registro de marcas</em></h1>
    <p>Respuestas claras a lo que más nos preguntan antes de registrar una marca en Argentina.</p>
  </header>

  <?php if ($items) : ?>
  <div class="buscar"><input type="search" id="smf-q" placeholder="Buscar una pregunta…" aria-label="Buscar una pregunta" autocomplete="off"></div>
  <?php endif; ?>

  <div class="caja">
    <?php if ($items) : ?>
    <div class="lista" id="smf-lista">
      <?php foreach ($items as $it) : ?>
      <a class="tarj" href="<?php echo esc_url($it['url']); ?>" data-t="<?php echo esc_attr(mb_strtolower($it['titulo'] . ' ' . $it['desc'], 'UTF-8')); ?>">
        <h2><?php echo esc_html($it['titulo']); ?></h2>
        <?php if ($it['desc'] !== '') : ?><p><?php echo esc_html($it['desc']); ?></p><?php endif; ?>
        <span>Leer la respuesta →</span>
      </a>
      <?php endforeach; ?>
    </div>
    <p class="vacio" id="smf-vacio" hidden>No encontramos esa pregunta. Probá con otras palabras o escribinos y te respondemos.</p>
    <?php else : ?>
    <p class="vacio">Estamos preparando las primeras respuestas. Mientras tanto, podés verificar tu marca o escribirnos.</p>
    <?php endif; ?>
  </div>

  <div class="cta"><div>
    <div><h2>¿Tu pregunta no está acá?</h2><p>Verificá tu marca gratis y te enviamos un <b>análisis preliminar sin cargo</b>.</p></div>
    <a class="btn" href="<?php echo esc_url($a['verificador']); ?>">Verificar mi marca</a>
  </div></div>

  <?php if ($items) : ?>
  <script type="application/ld+json"><?php echo wp_json_encode($ld, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES); ?></script>
  <script>
  (function () {
    var q = document.getElementById("smf-q"), lista = document.getElementById("smf-lista"), vacio = document.getElementById("smf-vacio");
    if (!q || !lista) return;
    function plano(s) { return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
    q.addEventListener("input", function () {
      var t = plano(q.value.trim()), hay = 0;
      Array.prototype.forEach.call(lista.children, function (a) {
        var ok = !t || plano(a.getAttribute("data-t")).indexOf(t) !== -1;
        a.hidden = !ok; if (ok) hay++;
      });
      vacio.hidden = hay > 0;
    });
  })();
  </script>
  <?php endif; ?>
</div>
        <?php
        return ob_get_clean();
    }
    add_shortcode('preguntas_frecuentes', 'smarties_preguntas_frecuentes');
}
