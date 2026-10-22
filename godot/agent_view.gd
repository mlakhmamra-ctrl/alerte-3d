extends Node3D
# Un habitant à l'écran : personnage en voxels de 1,70 m (style de la vidéo AGORA), orbe de pensée (LLM), éclaboussures dans l'eau.

const STATE_COL := {"home": Color("8fa3b8"), "preparing": Color("f2b134"), "walking": Color("f4f1e8"),
	"blocked": Color("e5484d"), "arrived": Color("46b97a"), "sheltered": Color("9b8cff")}
const STATE_FR := {"home": "chez soi", "preparing": "prépare son départ", "walking": "en route",
	"blocked": "bloqué(e)", "arrived": "à l'abri", "sheltered": "à l'abri chez soi (étage)"}

var aid := ""
var info := {}
var target := Vector3.ZERO
var vel := Vector3.ZERO
var state := "home"
var thinking := false
var depth := 0.0
var phase := 0.0
var flash := 0.0
var body_mat := StandardMaterial3D.new()
var vest_mat := StandardMaterial3D.new()
var id_mat := StandardMaterial3D.new()     # casquette de couleur propre à chaque habitant : son identifiant, sans texte
var id_col := Color.WHITE
var orb: MeshInstance3D
var orb_mat := StandardMaterial3D.new()
var label := Label3D.new()
var leg_l: Node3D
var leg_r: Node3D
var arm_l: Node3D
var arm_r: Node3D
var splash: CPUParticles3D
var bubble := Label3D.new()
var bubble_t := 0.0
var show_text := false      # étiquette et bulle : seulement en mode texte (touche I), la vue par défaut est sans texte

func setup(d: Dictionary, skin: Color, hair: Color, cloth: Color) -> void:
	info = d
	aid = d["id"]
	body_mat.albedo_color = cloth
	body_mat.roughness = 0.8
	vest_mat.albedo_color = STATE_COL["home"]
	vest_mat.emission_enabled = true
	vest_mat.emission = STATE_COL["home"]
	vest_mat.emission_energy_multiplier = 0.25
	var skin_m := StandardMaterial3D.new()
	skin_m.albedo_color = skin
	var hair_m := StandardMaterial3D.new()
	hair_m.albedo_color = hair
	var dark := StandardMaterial3D.new()
	dark.albedo_color = Color("2b2f36")
	leg_l = _limb(Vector3(-0.11, 0.86, 0), Vector3(0.2, 0.86, 0.22), dark)
	leg_r = _limb(Vector3(0.11, 0.86, 0), Vector3(0.2, 0.86, 0.22), dark)
	_cube(Vector3(0.44, 0.62, 0.26), body_mat, Vector3(0, 1.17, 0))
	_cube(Vector3(0.47, 0.36, 0.29), vest_mat, Vector3(0, 1.22, 0))          # gilet de couleur = état de décision
	arm_l = _limb(Vector3(-0.3, 1.46, 0), Vector3(0.14, 0.6, 0.16), body_mat)
	arm_r = _limb(Vector3(0.3, 1.46, 0), Vector3(0.14, 0.6, 0.16), body_mat)
	_cube(Vector3(0.3, 0.3, 0.3), skin_m, Vector3(0, 1.64, 0))                           # tête cubique
	_cube(Vector3(0.36, 0.12, 0.36), id_mat, Vector3(0, 1.82, -0.01))
	_cube(Vector3(0.36, 0.04, 0.16), id_mat, Vector3(0, 1.77, 0.24))             # visière
	_cube(Vector3(0.32, 0.2, 0.06), hair_m, Vector3(0, 1.7, -0.15))
	if d.get("kind", "rule") != "rule":       # orbe de pensée : le LLM réfléchit quand elle pulse
		orb = MeshInstance3D.new()
		var om := BoxMesh.new()                 # orbe cubique, tournée de 45° : un losange lumineux au-dessus de la tête
		om.size = Vector3.ONE * (0.2 if d["kind"] == "small" else 0.28)
		orb.mesh = om
		orb_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		orb_mat.albedo_color = Color("b48cff") if d["kind"] == "big" else Color("53d6ff")
		orb_mat.emission_enabled = true
		orb.material_override = orb_mat
		orb.position = Vector3(0, 2.15, 0)
		orb.rotation = Vector3(0.0, 0.0, PI * 0.25)
		add_child(orb)
	label.text = "%s · %s" % [aid, d.get("name", "")]
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.no_depth_test = true
	label.fixed_size = true
	label.pixel_size = 0.0009
	label.font_size = 24
	label.outline_size = 8
	label.outline_modulate = Color(0, 0, 0, 0.85)
	label.position = Vector3(0, 2.45, 0)
	label.visible = false
	add_child(label)
	bubble.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	bubble.no_depth_test = true
	bubble.fixed_size = true
	bubble.pixel_size = 0.001
	bubble.font_size = 20
	bubble.outline_size = 8
	bubble.outline_modulate = Color(0.05, 0.07, 0.1, 0.9)
	bubble.modulate = Color("fff6d8")
	bubble.width = 380
	bubble.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	bubble.position = Vector3(0, 3.05, 0)
	bubble.visible = false
	add_child(bubble)
	splash = CPUParticles3D.new()
	splash.amount = 24
	splash.lifetime = 0.5
	splash.emitting = false
	splash.direction = Vector3(0, 1, 0)
	splash.spread = 60.0
	splash.initial_velocity_min = 0.6
	splash.initial_velocity_max = 1.4
	splash.gravity = Vector3(0, -6, 0)
	splash.scale_amount_min = 0.03
	splash.scale_amount_max = 0.06
	var sm := SphereMesh.new()
	sm.radius = 0.5
	sm.height = 1.0
	splash.mesh = sm
	var spm := StandardMaterial3D.new()
	spm.albedo_color = Color(0.8, 0.82, 0.78, 0.7)
	spm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	splash.material_override = spm
	add_child(splash)

func _limb(pos: Vector3, size: Vector3, m: Material) -> Node3D:
	var pivot := Node3D.new()
	pivot.position = pos
	var mi := MeshInstance3D.new()
	var c := BoxMesh.new()
	c.size = size
	mi.mesh = c
	mi.material_override = m
	mi.position = Vector3(0, -size.y * 0.5, 0)
	pivot.add_child(mi)
	add_child(pivot)
	return pivot

func _cube(size: Vector3, m: Material, pos: Vector3) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var b := BoxMesh.new()
	b.size = size
	mi.mesh = b
	mi.material_override = m
	mi.position = pos
	add_child(mi)
	return mi

func set_id_color(c: Color) -> void:
	id_col = c
	id_mat.albedo_color = c
	id_mat.emission_enabled = true
	id_mat.emission = c
	id_mat.emission_energy_multiplier = 0.35

func set_state(s: String) -> void:
	if s == state:
		return
	state = s
	visible = s != "sheltered"                      # monté à l'étage : il n'est plus dans la rue
	var c: Color = STATE_COL.get(s, Color.WHITE)
	vest_mat.albedo_color = c
	vest_mat.emission = c
	vest_mat.emission_energy_multiplier = 0.6 if s in ["blocked", "arrived"] else 0.25

func say(txt: String, secs := 5.0) -> void:
	bubble.text = txt
	bubble.visible = show_text
	bubble_t = secs

func tick(delta: float, ground_y: float, sel: bool) -> void:
	var to := target - position
	to.y = 0
	var dist := to.length()
	var moving := dist > 0.05
	if moving:
		var step: float = min(dist, max(vel.length(), 1.2) * delta * 1.15)
		position += to.normalized() * step
		var ang := atan2(to.x, to.z)
		rotation.y = lerp_angle(rotation.y, ang, min(1.0, delta * 8.0))
		phase += delta * 7.0
	else:
		phase = lerp(phase, 0.0, min(1.0, delta * 5.0))
	position.y = lerp(position.y, ground_y, min(1.0, delta * 10.0))
	var sw := sin(phase) * (0.55 if moving else 0.0)
	leg_l.rotation.x = sw
	leg_r.rotation.x = -sw
	arm_l.rotation.x = -sw * 0.8
	arm_r.rotation.x = sw * 0.8
	if state == "home":
		arm_r.rotation.x = -0.4 if thinking else 0.0
	splash.emitting = moving and depth > 0.05
	splash.position = Vector3(0, max(0.05, depth), 0)
	if orb:
		var p := 0.5 + 0.5 * sin(Time.get_ticks_msec() * 0.008)
		orb_mat.emission = orb_mat.albedo_color
		orb_mat.emission_energy_multiplier = (1.5 + 3.0 * p) if thinking else 0.4
		orb.scale = Vector3.ONE * ((1.0 + 0.35 * p) if thinking else 0.8)
		orb.position.y = 2.15 + (0.06 * sin(Time.get_ticks_msec() * 0.004))
		orb.rotation.y += 0.02
	if state == "blocked":
		flash += delta * 6.0
		vest_mat.emission_energy_multiplier = 0.5 + 1.5 * abs(sin(flash))
	label.modulate = Color("ffe08a") if sel else Color(1, 1, 1, 0.92)
	label.visible = show_text and (sel or state != "arrived")
	label.text = "%s · %s%s" % [aid, info.get("name", ""), ("  …" if thinking else "")]
	if bubble_t > 0:
		bubble_t -= delta
		bubble.modulate.a = clamp(bubble_t, 0.0, 1.0)
		if bubble_t <= 0 or not show_text:
			bubble.visible = false
