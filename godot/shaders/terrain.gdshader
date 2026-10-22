// Sol du quartier : herbe, terre et vase près de la rivière, assombri quand il est mouillé.
// Style voxel (vidéo AGORA) : couleur calculée par « pixel » de 25 cm, sans lissage.
shader_type spatial;
render_mode diffuse_burley;
uniform sampler2D grain : repeat_enable, filter_nearest;
uniform sampler2D grain2 : repeat_enable, filter_nearest;
uniform float water_level = -1.35;
varying vec3 wpos;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void vertex() { wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	vec2 px = floor(wpos.xz * 4.0) / 4.0;
	float g = texture(grain, px * 0.08).r;
	float g2 = hash(px);
	vec3 grass = mix(vec3(0.27, 0.50, 0.17), vec3(0.40, 0.60, 0.22), g) * (0.88 + 0.2 * g2);
	vec3 mud = mix(vec3(0.62, 0.48, 0.30), vec3(0.72, 0.58, 0.38), g2);
	float bank = step(0.05, wpos.y + 0.15 * g);
	vec3 col = mix(mud, grass, bank) * COLOR.rgb;
	float wet = 1.0 - smoothstep(water_level - 0.02, water_level + 0.35, wpos.y);
	col *= mix(1.0, 0.6, wet);
	ALBEDO = col;
	ROUGHNESS = mix(0.95, 0.35, wet);
}
