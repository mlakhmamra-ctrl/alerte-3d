// Tranche du diorama : herbe en haut, puis couches de terre et de roche, en gros pixels (style voxel).
shader_type spatial;
render_mode diffuse_burley;
varying vec3 wpos;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void vertex() { wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	vec2 cell = floor(vec2(wpos.x + wpos.z, wpos.y) * 2.0);      // pixels de 50 cm
	float n = hash(cell);
	float y = cell.y / 2.0 + n * 0.6;
	vec3 grass = vec3(0.30, 0.52, 0.20);
	vec3 dirt = vec3(0.45, 0.30, 0.20);
	vec3 dirt2 = vec3(0.36, 0.24, 0.17);
	vec3 rock = vec3(0.33, 0.31, 0.30);
	vec3 col = rock;
	if (y > -9.0) col = dirt2;
	if (y > -5.0) col = dirt;
	if (y > -2.6 && wpos.y > -0.6) col = grass;
	ALBEDO = col * (0.85 + 0.25 * n);
	ROUGHNESS = 1.0;
}
