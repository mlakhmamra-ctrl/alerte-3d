// Eau de crue : bleue, animée, écume en gros pixels (style de la vidéo AGORA). Le plan monte avec le niveau envoyé par la simulation.
shader_type spatial;
render_mode blend_mix, depth_draw_always, cull_disabled, specular_schlick_ggx;

uniform vec3 shallow : source_color = vec3(0.24, 0.55, 0.70);
uniform vec3 deep : source_color = vec3(0.10, 0.24, 0.42);
uniform float flow_speed = 0.6;
uniform float storm = 0.0;           // 0 calme -> 1 crue forte : plus de vagues et d'écume
uniform sampler2D noise_a : hint_normal, repeat_enable, filter_linear_mipmap;
uniform sampler2D noise_b : hint_normal, repeat_enable, filter_linear_mipmap;
uniform sampler2D foam_tex : repeat_enable, filter_linear_mipmap;
varying vec3 wpos;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	float a = 0.03 + 0.06 * storm;
	VERTEX.y += a * sin(wpos.x * 0.35 + TIME * 1.6) * cos(wpos.z * 0.27 + TIME * 1.1);
}

void fragment() {
	vec2 uv = wpos.xz * 0.06;
	vec2 flow = vec2(TIME * 0.05 * flow_speed, 0.0);
	vec3 n1 = texture(noise_a, uv + flow).rgb;
	vec3 n2 = texture(noise_b, uv * 1.7 - flow * 1.3 + vec2(0.0, TIME * 0.01)).rgb;
	NORMAL_MAP = normalize(mix(n1, n2, 0.5));
	NORMAL_MAP_DEPTH = 0.6 + 0.8 * storm;
	float fres = pow(1.0 - clamp(dot(NORMAL, VIEW), 0.0, 1.0), 3.0);
	float f = texture(foam_tex, floor((uv * 3.0 + flow * 2.0) * 24.0) / 24.0).r;
	float foam = step(0.86 - 0.08 * storm, f) * (0.3 + 0.5 * storm);
	vec3 col = mix(deep, shallow, 0.35 + 0.4 * f);
	ALBEDO = mix(col, vec3(0.92, 0.95, 0.97), foam);
	ROUGHNESS = 0.04 + foam * 0.4;
	METALLIC = 0.15;
	SPECULAR = 0.5;
	ALPHA = clamp(0.8 + fres * 0.18 + foam * 0.2, 0.0, 0.97);
}
