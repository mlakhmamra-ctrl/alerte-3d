// Feuillage qui ondule au vent de la tempête (le vent forcit avec la crue).
shader_type spatial;
render_mode cull_back, diffuse_burley;
uniform vec3 color : source_color = vec3(0.25, 0.45, 0.2);
uniform float wind = 0.4;
uniform sampler2D grain : repeat_enable, filter_linear_mipmap;
varying float h;
void vertex() {
	vec3 w = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	h = VERTEX.y;
	float s = sin(TIME * 1.7 + w.x * 0.3 + w.z * 0.2) + 0.5 * sin(TIME * 3.1 + w.z * 0.5);
	VERTEX.x += s * 0.08 * wind * (VERTEX.y + 1.5);
	VERTEX.z += s * 0.05 * wind * (VERTEX.y + 1.5);
}
void fragment() {
	vec3 w = (INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz;
	float g = texture(grain, w.xz * 0.35 + w.y * 0.2).r;
	ALBEDO = color * (0.75 + 0.5 * g);
	ROUGHNESS = 0.85;
}
