extends Node3D
# Construit Rive-Claire à partir de run.start : relief, rivière, rues, ponts, maisons, école, gymnase, place, végétation.
# Les dimensions suivent la fiche ALERTE (1 unité = 1 m). Tout est procédural : aucun fichier 3D externe.

var hm := {}
var nodes := {}
var edges := []
var refuges := []
var rng := RandomNumberGenerator.new()
var blockers := []          # rectangles occupés (x0, z0, x1, z1) pour placer maisons et arbres sans chevauchement
var noise_a: NoiseTexture2D
var noise_b: NoiseTexture2D
var noise_c: NoiseTexture2D
var water: MeshInstance3D
var water_mat: ShaderMaterial
var terrain_mat: ShaderMaterial
var foliage_mats := []
var lamps := []
var cells: MultiMeshInstance3D
var barrier: Node3D
var barrier_light: OmniLight3D
var siren: OmniLight3D
var siren_mesh: MeshInstance3D
var refuge_labels := {}
var refuge_rims := {}
var refuge_slots := {}      # une lanterne par place du refuge : allumée = place occupée (pas de texte)
var labels := []            # tous les textes 3D, cachés par défaut (consigne : pas de texte dans la visualisation)
var show_labels := false
var S := 1.0                # WORLD_SCALE envoyé par la simulation (1 = plan exact de la fiche)
var RZ := -2.0              # axe de la rivière
var PX := -40.0             # axe du Passage des Hauteurs
var MX := 60.0              # demi-largeur du quartier simulé (X)
var MZ := 40.0              # demi-profondeur du quartier simulé (Z)
var OUT_X := 200.0          # monde agrandi : le diorama dépasse le quartier simulé de 140 m en X et 120 m en Z
var OUT_Z := 160.0
const BASE_Y := -16.0       # monde en diorama (style de la vidéo AGORA) : un bloc de terre suspendu dans le ciel du soir
var pix_tex: NoiseTexture2D  # petite texture de bruit filtrée « au plus proche » : aspect pixel / voxel sur tous les matériaux
var debris: CPUParticles3D
var water_ends := []
var mat_cache := {}
var poste_pos := Vector3(8, 0, 16)
const BRIDGE_Y := 1.25
const PASSAGE_Y := 1.0

# ---------- relief ----------
func h_at(x: float, z: float) -> float:
	if hm.is_empty():
		return 0.0
	var st: float = hm["step"]
	var fx: float = clamp((x - hm["x0"]) / st, 0.0, hm["nx"] - 1.001)
	var fz: float = clamp((z - hm["z0"]) / st, 0.0, hm["nz"] - 1.001)
	var i := int(fx)
	var k := int(fz)
	var u := fx - i
	var v := fz - k
	var n: int = hm["nx"]
	var h: Array = hm["h"]
	var a: float = h[k * n + i]
	var b: float = h[k * n + i + 1]
	var c: float = h[(k + 1) * n + i]
	var d: float = h[(k + 1) * n + i + 1]
	return lerp(lerp(a, b, u), lerp(c, d, u), v)

func h_ext(x: float, z: float) -> float:   # relief au-delà du quartier simulé : collines douces, la rivière continue
	var y := h_at(x, z)
	var ex: float = max(0.0, abs(x) - MX)
	var ez: float = max(0.0, abs(z) - MZ)
	var e := sqrt(ex * ex + ez * ez)
	if e <= 0.0:
		return y
	var r := smoothstep(9.0, 18.0, abs(z - RZ))               # 0 dans le lit de la rivière
	var n := 0.5 + 0.5 * noise_c.noise.get_noise_2d(x * 0.5, z * 0.5)
	return y + (0.015 * e + 3.0 * smoothstep(0.0, 90.0, e) * n) * r

func walk_y(x: float, z: float) -> float:   # hauteur où marche un habitant (tablier des ponts et parvis compris)
	for r in refuges:
		if Vector2(x - r["x"], z - r["z"]).length() < 6.0:
			return h_at(r["x"], r["z"]) + 1.2
	if abs(x) <= 2.75 and z >= RZ - 9.0 and z <= RZ + 9.0:
		return BRIDGE_Y + 0.15
	if abs(x - PX) <= 2.25 and z >= RZ - 13.5 and z <= RZ + 10.5:
		var t: float = clamp((RZ - 10.0 - z) / 3.5, 0.0, 1.0)      # escaliers côté nord
		return PASSAGE_Y + 0.12 + t * (h_at(PX, RZ - 13.5) - PASSAGE_Y)
	return h_at(x, z)

func mat(c: Color, rough := 0.85, metal := 0.0, emit := 0.0) -> StandardMaterial3D:
	var key := "%s_%s_%s_%s" % [c.to_html(), rough, metal, emit]
	if mat_cache.has(key):
		return mat_cache[key]
	var m := StandardMaterial3D.new()
	m.albedo_color = c
	m.roughness = rough
	m.metallic = metal
	if pix_tex and emit <= 0:
		m.albedo_texture = pix_tex
		m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS
		m.uv1_triplanar = true
		m.uv1_world_triplanar = true
		m.uv1_scale = Vector3(0.5, 0.5, 0.5)
	if emit > 0:
		m.emission_enabled = true
		m.emission = c
		m.emission_energy_multiplier = emit
	mat_cache[key] = m
	return m

func box(p: Node, size: Vector3, m: Material, pos: Vector3, rot_y := 0.0) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var b := BoxMesh.new()
	b.size = size
	mi.mesh = b
	mi.material_override = m
	mi.position = pos
	mi.rotation.y = rot_y
	p.add_child(mi)
	return mi

func cyl(p: Node, r_top: float, r_bot: float, h: float, m: Material, pos: Vector3, seg := 16) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var c := CylinderMesh.new()
	c.top_radius = r_top
	c.bottom_radius = r_bot
	c.height = h
	c.radial_segments = seg
	mi.mesh = c
	mi.material_override = m
	mi.position = pos
	p.add_child(mi)
	return mi

func sph(p: Node, r: float, m: Material, pos: Vector3) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var s := SphereMesh.new()
	s.radius = r
	s.height = r * 2
	s.radial_segments = 12
	s.rings = 6
	mi.mesh = s
	mi.material_override = m
	mi.position = pos
	p.add_child(mi)
	return mi

func text3d(p: Node, t: String, pos: Vector3, size := 48, col := Color.WHITE, billboard := true) -> Label3D:
	var l := Label3D.new()
	l.text = t
	l.font_size = size
	l.pixel_size = 0.012
	l.outline_size = 10
	l.outline_modulate = Color(0, 0, 0, 0.8)
	l.modulate = col
	l.position = pos
	if billboard:
		l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.visible = show_labels
	labels.append(l)
	p.add_child(l)
	return l

func set_labels_visible(v: bool) -> void:
	show_labels = v
	for l in labels:
		if is_instance_valid(l):
			l.visible = v

func _noise(freq: float, seed_: int, normal := false) -> NoiseTexture2D:
	var n := FastNoiseLite.new()
	n.frequency = freq
	n.seed = seed_
	n.fractal_octaves = 4
	var t := NoiseTexture2D.new()
	t.noise = n
	t.width = 256
	t.height = 256
	t.seamless = true
	t.as_normal_map = normal
	t.generate_mipmaps = true
	return t

func free_spot(x0: float, z0: float, x1: float, z1: float, margin := 0.5) -> bool:
	for b in blockers:
		if x1 + margin > b[0] and x0 - margin < b[2] and z1 + margin > b[1] and z0 - margin < b[3]:
			return false
	return true

func block(x0: float, z0: float, x1: float, z1: float) -> void:
	blockers.append([x0, z0, x1, z1])

# ---------- construction ----------
func build(d: Dictionary) -> void:
	for c in get_children():
		c.queue_free()
	blockers.clear()
	lamps.clear()
	foliage_mats.clear()
	refuge_labels.clear()
	refuge_rims.clear()
	refuge_slots.clear()
	labels.clear()
	water_ends.clear()                         # sinon, au run suivant, on modifierait des nœuds déjà libérés
	rng.seed = 42
	hm = d["heightmap"]
	nodes = d["streets"]["nodes"]
	edges = d["streets"]["edges"]
	refuges = d["refuges"]
	var m: Dictionary = d["map"]
	S = float(m.get("world_scale", 1.0))
	RZ = float(m.get("river_z", -2.0))
	PX = float(m.get("pass_x", -40.0 * S))
	MX = float(m["x1"])
	MZ = float(m["z1"])
	OUT_X = MX + 140.0
	OUT_Z = MZ + 120.0
	noise_a = _noise(0.02, 1, true)
	noise_b = _noise(0.035, 2, true)
	noise_c = _noise(0.05, 3)
	_pixel_texture()
	mat_cache.clear()
	_terrain()
	_diorama_sides()
	_water()
	_cells(d["map"])
	block(-MX, RZ - 7, MX, RZ + 7)              # rivière
	_streets()
	_bridges()
	_place_and_poste()
	_school()
	_gym()
	_new_refuge_buildings()
	for r in refuges:
		_refuge(r)
	_houses(d["agents"])
	_trees()
	_street_furniture()
	_outskirts()

func _terrain() -> void:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var xs := _axis(OUT_X, MX)            # 1,25 m dans le quartier, 5 m dans les faubourgs
	var zs := _axis(OUT_Z, MZ)
	var nx := xs.size()
	var nz := zs.size()
	for k in nz:
		for i in nx:
			var x: float = xs[i]
			var z: float = zs[k]
			var y := h_ext(x, z)
			var tint := 0.92 + 0.16 * noise_c.noise.get_noise_2d(x * 3.0, z * 3.0)
			st.set_color(Color(tint, tint, tint))
			st.set_uv(Vector2(x, z) * 0.1)
			st.add_vertex(Vector3(x, y, z))
	for k in nz - 1:
		for i in nx - 1:
			var a := k * nx + i
			st.add_index(a)
			st.add_index(a + 1)
			st.add_index(a + nx)
			st.add_index(a + 1)
			st.add_index(a + nx + 1)
			st.add_index(a + nx)
	st.generate_normals()
	var mi := MeshInstance3D.new()
	mi.mesh = st.commit()
	terrain_mat = ShaderMaterial.new()
	terrain_mat.shader = load("res://shaders/terrain.gdshader")
	terrain_mat.set_shader_parameter("grain", _noise(0.03, 5))
	terrain_mat.set_shader_parameter("grain2", _noise(0.2, 6))
	mi.material_override = terrain_mat
	add_child(mi)

func _axis(outer: float, inner: float) -> Array:
	var v := []
	for seg in [[-outer, -inner, 5.0], [-inner, inner, 1.25], [inner, outer + 0.01, 5.0]]:
		var x: float = seg[0]
		while x < seg[1] - 0.01:
			v.append(x)
			x += seg[2]
	v.append(outer)
	return v

func _pixel_texture() -> void:
	var n := FastNoiseLite.new()
	n.noise_type = FastNoiseLite.TYPE_VALUE
	n.frequency = 0.9
	n.seed = 11
	pix_tex = NoiseTexture2D.new()
	pix_tex.noise = n
	pix_tex.width = 16
	pix_tex.height = 16
	pix_tex.seamless = true
	var g := Gradient.new()
	g.set_color(0, Color(0.8, 0.8, 0.8))
	g.set_color(1, Color(1, 1, 1))
	pix_tex.color_ramp = g

func _diorama_sides() -> void:
	# bords du bloc : couches de terre visibles (herbe, terre, roche), comme une maquette posée dans le ciel
	var soil := ShaderMaterial.new()
	soil.shader = load("res://shaders/soil.gdshader")
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var xs := _axis(OUT_X, MX)
	var zs := _axis(OUT_Z, MZ)
	for side in 4:
		var pts := []
		if side == 0:
			for x in xs: pts.append(Vector2(x, -OUT_Z))
		elif side == 1:
			for z in zs: pts.append(Vector2(OUT_X, z))
		elif side == 2:
			for i in range(xs.size() - 1, -1, -1): pts.append(Vector2(xs[i], OUT_Z))
		else:
			for i in range(zs.size() - 1, -1, -1): pts.append(Vector2(-OUT_X, zs[i]))
		for i in pts.size() - 1:
			var a: Vector2 = pts[i]
			var b: Vector2 = pts[i + 1]
			var ta := Vector3(a.x, h_ext(a.x, a.y), a.y)
			var tb := Vector3(b.x, h_ext(b.x, b.y), b.y)
			var ba := Vector3(a.x, BASE_Y, a.y)
			var bb := Vector3(b.x, BASE_Y, b.y)
			for q in [ta, ba, tb, tb, ba, bb]:
				st.add_vertex(q)
	st.generate_normals()
	var mi := MeshInstance3D.new()
	mi.mesh = st.commit()
	mi.material_override = soil
	add_child(mi)
	var bottom := box(self, Vector3(OUT_X * 2, 0.4, OUT_Z * 2), mat(Color("3b2a22"), 1.0), Vector3(0, BASE_Y, 0))
	bottom.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	for sx in [-1.0, 1.0]:                               # la rivière sort du bloc : tranche d'eau visible aux deux extrémités
		var wm := StandardMaterial3D.new()
		wm.albedo_color = Color(0.2, 0.42, 0.58, 0.82)
		wm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		wm.roughness = 0.1
		var w := box(self, Vector3(0.2, 1.0, 13.0), wm, Vector3(sx * (OUT_X + 0.05), -1.4, RZ))
		w.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		water_ends.append(w)

func _water() -> void:
	water = MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(OUT_X * 2, OUT_Z * 2)
	pm.subdivide_width = int(OUT_X)
	pm.subdivide_depth = int(OUT_Z)
	water.mesh = pm
	water_mat = ShaderMaterial.new()
	water_mat.shader = load("res://shaders/water.gdshader")
	water_mat.set_shader_parameter("noise_a", noise_a)
	water_mat.set_shader_parameter("noise_b", noise_b)
	water_mat.set_shader_parameter("foam_tex", noise_c)
	water.material_override = water_mat
	water.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	water.position = Vector3(0, -1.35, 0)
	add_child(water)
	debris = CPUParticles3D.new()                    # objets emportés par la crue (petits cubes colorés, comme dans la vidéo)
	debris.amount = 160
	debris.lifetime = 14.0
	debris.preprocess = 14.0
	debris.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	debris.emission_box_extents = Vector3(MX, 0.05, 11.0 * S + 11.0)
	debris.position = Vector3(0, -1.35, RZ + 8.0 * S)
	debris.direction = Vector3(1, 0, 0)
	debris.spread = 25.0
	debris.gravity = Vector3.ZERO
	debris.initial_velocity_min = 0.6
	debris.initial_velocity_max = 1.6
	debris.angular_velocity_min = -40.0
	debris.angular_velocity_max = 40.0
	debris.scale_amount_min = 0.25
	debris.scale_amount_max = 0.7
	var dm := BoxMesh.new()
	dm.size = Vector3(1, 0.4, 0.7)
	debris.mesh = dm
	var dmat := StandardMaterial3D.new()
	dmat.vertex_color_use_as_albedo = true
	debris.material_override = dmat
	var cr := Gradient.new()
	cr.offsets = PackedFloat32Array([0.0, 0.25, 0.5, 0.75, 1.0])
	cr.colors = PackedColorArray([Color("e5484d"), Color("f2b134"), Color("f4f1e8"), Color("4da3ff"), Color("3ddc84")])
	debris.color_initial_ramp = cr
	debris.emitting = false
	debris.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(debris)

func set_level(y: float, storm: float) -> void:
	if water:
		water.position.y = y
		water_mat.set_shader_parameter("storm", storm)
		terrain_mat.set_shader_parameter("water_level", y)
		debris.position.y = y + 0.05
		debris.emitting = storm > 0.45
		for w in water_ends:
			w.scale.y = max(0.05, y + 1.5)
			w.position.y = (y - 1.5) * 0.5
	for fmm in foliage_mats:
		fmm.set_shader_parameter("wind", 0.35 + storm * 0.9)

func _cells(m: Dictionary) -> void:
	# cellules de crue de 5 m x 5 m (fiche) : couleur selon la profondeur, touche G pour les montrer/cacher
	cells = MultiMeshInstance3D.new()
	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.use_colors = true
	var q := PlaneMesh.new()
	q.size = Vector2(4.6, 4.6)
	mm.mesh = q
	mm.instance_count = int(m["nx"]) * int(m["nz"])
	var cm := StandardMaterial3D.new()
	cm.vertex_color_use_as_albedo = true
	cm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	cm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	cm.no_depth_test = false
	q.material = cm
	cells.multimesh = mm
	cells.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(cells)
	var n := 0
	for k in int(m["nz"]):
		for i in int(m["nx"]):
			var x: float = m["x0"] + (i + 0.5) * m["cell"]
			var z: float = m["z0"] + (k + 0.5) * m["cell"]
			mm.set_instance_transform(n, Transform3D(Basis(), Vector3(x, h_at(x, z) + 0.05, z)))
			mm.set_instance_color(n, Color(0, 0, 0, 0))
			n += 1

func set_cells(depths: Array, level: float) -> void:
	if not cells:
		return
	var mm := cells.multimesh
	for n in min(depths.size(), mm.instance_count):
		var dcm: float = depths[n]
		var t := mm.get_instance_transform(n)
		t.origin.y = max(level, h_at(t.origin.x, t.origin.z)) + 0.04
		mm.set_instance_transform(n, t)
		if dcm <= 0:
			mm.set_instance_color(n, Color(0, 0, 0, 0))
		else:
			var c := Color("ffe066").lerp(Color("ff8c42"), clamp(dcm / 30.0, 0, 1)).lerp(Color("d7263d"), clamp((dcm - 30) / 25.0, 0, 1))
			c.a = 0.26
			mm.set_instance_color(n, c)

func _segment(a: Vector2, b: Vector2, w: float, m: Material, y_off: float, thick := 0.12) -> void:
	var L := a.distance_to(b)
	var n := int(max(1, ceil(L / 4.0)))
	var dir := (b - a) / L
	var ang := atan2(dir.x, dir.y)
	for i in n:
		var p0 := a + dir * (L * i / n)
		var p1 := a + dir * (L * (i + 1) / n)
		var mid := (p0 + p1) * 0.5
		var y: float = max(h_at(p0.x, p0.y), h_at(p1.x, p1.y), h_at(mid.x, mid.y))
		box(self, Vector3(w, thick, L / n + 0.6), m, Vector3(mid.x, y + y_off, mid.y), ang)

func _streets() -> void:
	var asphalt := mat(Color("3a3d42"), 0.75)
	var walk := mat(Color("9a958c"), 0.9)
	var line := mat(Color("e8e4d8"), 0.6)
	for e in edges:
		var nm: String = e[3]
		if nm == "Pont Central" or nm == "Passage des Hauteurs":
			continue
		var a: Vector2 = Vector2(nodes[e[0]][0], nodes[e[0]][1])
		var b: Vector2 = Vector2(nodes[e[1]][0], nodes[e[1]][1])
		if a.distance_to(b) < 0.3:
			continue
		var w: float = e[2]
		if nm == "allée":
			_segment(a, b, 1.6, walk, 0.03, 0.08)
			continue
		_segment(a, b, w, asphalt, 0.02)
		if w >= 6.0:                                    # trottoirs de 1,5 m des rues principales
			var dir := (b - a).normalized()
			var nrm := Vector2(-dir.y, dir.x) * (w * 0.5 + 0.75)
			_segment(a + nrm, b + nrm, 1.5, walk, 0.08, 0.16)
			_segment(a - nrm, b - nrm, 1.5, walk, 0.08, 0.16)
			var L := a.distance_to(b)
			var k := 0.0
			while k < L - 2.0:
				var p := a + dir * (k + 1.0)
				box(self, Vector3(0.15, 0.13, 1.6), line, Vector3(p.x, h_at(p.x, p.y) + 0.03, p.y), atan2(dir.x, dir.y))
				k += 4.0
		var mg: float = 1.5 if w >= 6.0 else 0.3
		var x0: float = min(a.x, b.x) - w * 0.5 - mg
		var x1: float = max(a.x, b.x) + w * 0.5 + mg
		var z0: float = min(a.y, b.y) - w * 0.5 - mg
		var z1: float = max(a.y, b.y) + w * 0.5 + mg
		block(x0, z0, x1, z1)

func _bridges() -> void:
	var stone := mat(Color("8d8a82"), 0.9)
	var deck := mat(Color("4a4c50"), 0.7)
	var rail := mat(Color("2f3a44"), 0.4, 0.6)
	# Pont Central : 18 m, chaussée 5,5 m, sans marche
	var p := Node3D.new()
	add_child(p)
	box(p, Vector3(5.5, 0.5, 18.6), deck, Vector3(0, BRIDGE_Y, RZ))
	box(p, Vector3(6.6, 0.35, 18.6), stone, Vector3(0, BRIDGE_Y - 0.4, RZ))
	for s in [-1, 1]:
		box(p, Vector3(0.12, 1.0, 18.4), rail, Vector3(s * 3.0, BRIDGE_Y + 0.6, RZ))
		box(p, Vector3(0.18, 0.08, 18.4), rail, Vector3(s * 3.0, BRIDGE_Y + 1.1, RZ))
		for z in [RZ - 6.5, RZ, RZ + 6.5]:
			cyl(p, 0.09, 0.09, 4.2, rail, Vector3(s * 3.2, BRIDGE_Y + 2.1, z), 8)
			var lan := sph(p, 0.2, mat(Color("ffd89a"), 0.3, 0.0, 2.5), Vector3(s * 3.2, BRIDGE_Y + 4.3, z))
			lamps.append(lan)
	for z in [RZ - 4.0, RZ + 4.0]:
		box(p, Vector3(5.0, 3.2, 1.4), stone, Vector3(0, -0.6, z))
	text3d(p, "Pont Central", Vector3(4.5, BRIDGE_Y + 2.6, RZ + 9.5), 40, Color("e6eef5"))
	# barrière (visible quand le pont est fermé)
	barrier = Node3D.new()
	barrier.position = Vector3(0, BRIDGE_Y + 0.25, RZ + 8.0)
	barrier.visible = false
	p.add_child(barrier)
	for i in 6:
		box(barrier, Vector3(0.9, 0.35, 0.15), mat(Color("e5484d") if i % 2 == 0 else Color("f5f5f5"), 0.5), Vector3(-2.25 + i * 0.9, 0.9, 0))
	for s in [-1, 1]:
		box(barrier, Vector3(0.15, 1.1, 0.15), rail, Vector3(s * 2.7, 0.55, 0))
	barrier_light = OmniLight3D.new()
	barrier_light.light_color = Color("ff3b30")
	barrier_light.omni_range = 9.0
	barrier_light.light_energy = 0.0
	barrier_light.position = Vector3(0, 1.6, 0)
	barrier.add_child(barrier_light)
	text3d(barrier, "PONT FERMÉ", Vector3(0, 2.2, 0), 52, Color("ff6b6b"))
	# Passage des Hauteurs : passerelle de 24 m, 4,5 m de large, escaliers côté nord
	var w := Node3D.new()
	add_child(w)
	var wood := mat(Color("7a5a3a"), 0.9)
	box(w, Vector3(4.5, 0.3, 20.5), wood, Vector3(PX, PASSAGE_Y, RZ + 0.25))
	for s in [-1, 1]:
		box(w, Vector3(0.1, 0.9, 20.5), wood, Vector3(PX + s * 2.2, PASSAGE_Y + 0.55, RZ + 0.25))
	for z in [RZ - 6.0, RZ + 6.0]:
		cyl(w, 0.25, 0.3, 3.0, stone, Vector3(PX, PASSAGE_Y - 1.5, z), 8)
	var top := h_at(PX, RZ - 13.5)
	for i in 8:                                      # escaliers
		var t := (i + 0.5) / 8.0
		box(w, Vector3(4.5, 0.25, 0.48), stone, Vector3(PX, lerp(PASSAGE_Y, top, t), RZ - 10.0 - t * 3.5))
	text3d(w, "Passage des Hauteurs (escaliers)", Vector3(PX - 4.5, PASSAGE_Y + 2.4, RZ + 10.0), 34, Color("e6eef5"))

func set_bridge(open: bool) -> void:
	if barrier:
		barrier.visible = not open

func _place_and_poste() -> void:
	var paving := mat(Color("b8b0a0"), 0.9)
	var c := Vector2(2, 13) * S                     # place de 18 x 12 m
	box(self, Vector3(18, 0.14, 12), paving, Vector3(c.x, h_at(c.x, c.y) + 0.07, c.y))
	block(c.x - 9, c.y - 6, c.x + 9, c.y + 6)
	var basin := cyl(self, 1.6, 1.8, 0.6, mat(Color("9b968c")), Vector3(c.x - 3, h_at(c.x - 3, c.y + 1) + 0.3, c.y + 1))
	cyl(self, 0.25, 0.25, 1.6, mat(Color("9b968c")), Vector3(c.x - 3, h_at(c.x - 3, c.y + 1) + 1.0, c.y + 1))
	basin.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	for dx in [-7.0, 7.0]:
		box(self, Vector3(2.0, 0.45, 0.6), mat(Color("6b4f36")), Vector3(c.x + dx, h_at(c.x + dx, c.y - 3.5) + 0.35, c.y - 3.5))
	text3d(self, "Place du quartier", Vector3(c.x, 3.2, c.y), 34, Color("f1ead8"))
	# poste municipal : 8 x 6 m, 4,5 m de haut, émetteur des alertes
	poste_pos = Vector3(8 * S, h_at(8 * S, 16 * S), 16 * S)
	var p := Node3D.new()
	p.position = poste_pos
	add_child(p)
	box(p, Vector3(8, 4.5, 6), mat(Color("d9d3c4")), Vector3(0, 2.25, 0))
	box(p, Vector3(8.4, 0.4, 6.4), mat(Color("5b6470")), Vector3(0, 4.7, 0))
	box(p, Vector3(1.4, 2.3, 0.1), mat(Color("2c3e50")), Vector3(0, 1.15, -3.02))
	for x in [-2.6, 2.6]:
		box(p, Vector3(1.6, 1.2, 0.08), mat(Color("ffe9b0"), 0.2, 0.0, 0.6), Vector3(x, 2.6, -3.02))
	cyl(p, 0.05, 0.05, 4.0, mat(Color("cccccc"), 0.3, 0.8), Vector3(3.2, 6.9, 2.0), 6)
	box(p, Vector3(1.2, 0.8, 0.03), mat(Color("2a5db0")), Vector3(3.8, 8.4, 2.0))
	siren_mesh = cyl(p, 0.3, 0.35, 0.5, mat(Color("ff9f1c"), 0.3, 0.0, 0.3), Vector3(-2.5, 5.2, 0), 12)
	siren = OmniLight3D.new()
	siren.light_color = Color("ff9f1c")
	siren.omni_range = 26.0
	siren.light_energy = 0.0
	siren.position = Vector3(-2.5, 5.6, 0)
	p.add_child(siren)
	text3d(p, "Poste municipal", Vector3(0, 6.0, -3.2), 32, Color("f1ead8"))

func _windows(p: Node3D, w: float, d: float, h: float, floors: int, lit := 0.25) -> void:
	var glass := mat(Color("ffd9a0"), 0.2, 0.0, lit)
	var dark := mat(Color("2a3340"), 0.15, 0.3)
	for f in floors:
		var y: float = 1.6 + f * (h - 1.0) / max(1, floors)
		var nw := int(w / 3.0)
		for i in nw:
			var x := -w * 0.5 + (i + 0.5) * w / nw
			var m := glass if rng.randf() < 0.45 else dark
			box(p, Vector3(1.1, 1.2, 0.08), m, Vector3(x, y, -d * 0.5 - 0.02))
			box(p, Vector3(1.1, 1.2, 0.08), m, Vector3(x, y, d * 0.5 + 0.02))

func _school() -> void:
	# École des Tilleuls : 22 x 16 m, 7,5 m de haut, centre (-36, +25)
	var c := Vector2(-36, 25) * S
	var p := Node3D.new()
	p.position = Vector3(c.x, h_at(c.x, c.y), c.y)
	add_child(p)
	box(p, Vector3(22, 7.5, 16), mat(Color("e8dcc0")), Vector3(0, 3.75, 0))
	box(p, Vector3(22.6, 0.5, 16.6), mat(Color("8c3b2e")), Vector3(0, 7.75, 0))
	box(p, Vector3(4, 2.8, 0.2), mat(Color("4a5a6a")), Vector3(-11.05, 1.4, 0), PI * 0.5)
	_windows(p, 22, 16, 7.5, 2)
	text3d(p, "ÉCOLE DES TILLEULS", Vector3(0, 9.0, 0), 44, Color("fff3d6"))
	block(c.x - 11.5, c.y - 8.5, c.x + 11.5, c.y + 8.5)
	for i in 10:
		var lx := -10.0 + i * 2.2
		cyl(p, 0.04, 0.04, 1.2, mat(Color("5a6470"), 0.4, 0.6), Vector3(lx, 0.6, -9.0), 6)

func _gym() -> void:
	# Gymnase des Hauteurs : 26 x 18 m, 9 m de haut, centre (+30, -25)
	var c := Vector2(30, -25) * S
	var p := Node3D.new()
	p.position = Vector3(c.x, h_at(c.x, c.y), c.y)
	add_child(p)
	box(p, Vector3(26, 6.5, 18), mat(Color("c9d4dc")), Vector3(0, 3.25, 0))
	var roof := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = 9.2
	cm.bottom_radius = 9.2
	cm.height = 26.4
	cm.radial_segments = 24
	roof.mesh = cm
	roof.material_override = mat(Color("6d7f8c"), 0.4, 0.5)
	roof.rotation.z = PI * 0.5
	roof.position = Vector3(0, 6.5 - 6.7, 0)
	roof.scale = Vector3(1, 1, 1)
	p.add_child(roof)
	for i in 8:
		box(p, Vector3(2.4, 2.2, 0.08), mat(Color("9fd3ff"), 0.15, 0.2, 0.15), Vector3(-10.5 + i * 3.0, 4.2, 9.02))
	box(p, Vector3(0.2, 3.0, 5.0), mat(Color("3d4b57")), Vector3(-13.05, 1.5, 0))
	text3d(p, "GYMNASE DES HAUTEURS", Vector3(0, 11.5, 0), 44, Color("e9f6ff"))
	block(c.x - 13.5, c.y - 9.5, c.x + 13.5, c.y + 9.5)

const REFUGE_COL := {"ecole": Color("3d8bfd"), "gymnase": Color("2fbf71"), "salle": Color("e07a5f"), "eglise": Color("ff8fab"), "college": Color("2ec4b6")}

func _hall(c: Vector2, size: Vector3, wall: Color, roof: Color, label: String) -> Node3D:
	var p := Node3D.new()
	p.position = Vector3(c.x, h_at(c.x, c.y), c.y)
	add_child(p)
	box(p, size, mat(wall), Vector3(0, size.y * 0.5, 0))
	box(p, Vector3(size.x + 0.6, 0.5, size.z + 0.6), mat(roof), Vector3(0, size.y + 0.25, 0))
	_windows(p, size.x, size.z, size.y, 2)
	text3d(p, label, Vector3(0, size.y + 1.5, 0), 44, Color("fff3d6"))
	block(c.x - size.x * 0.5 - 0.5, c.y - size.z * 0.5 - 0.5, c.x + size.x * 0.5 + 0.5, c.y + size.z * 0.5 + 0.5)
	return p

func _new_refuge_buildings() -> void:
	# MONDE=etendu : les trois refuges ajoutés, chacun à côté de son parvis (positions du plan × WORLD_SCALE)
	for r in refuges:
		match r["id"]:
			"salle":                                     # Salle des Fêtes du Moulin : 18 x 8 m, sur la butte
				_hall(Vector2(90, 30.5) * S, Vector3(18, 6, 8), Color("efd9b4"), Color("9a4b32"), "SALLE DES FÊTES")
			"eglise":                                    # Église Saint-Roch : nef de 20 x 12 m et clocher
				var p := _hall(Vector2(-93, -28) * S, Vector3(20, 8, 12), Color("d8d0c0"), Color("5b6470"), "ÉGLISE SAINT-ROCH")
				box(p, Vector3(4, 16, 4), mat(Color("cfc6b4")), Vector3(11, 8, 0))
				cyl(p, 0.0, 2.6, 5.0, mat(Color("4f5b66"), 0.6), Vector3(11, 18.5, 0), 4)
			"college":                                   # Collège du Plateau : 22 x 14 m, deux étages
				_hall(Vector2(88, -25) * S, Vector3(22, 8, 14), Color("dfe6ea"), Color("4f5b66"), "COLLÈGE DU PLATEAU")

func _refuge(r: Dictionary) -> void:
	# parvis surélevé de 1,2 m, zone d'arrivée de 12 m de diamètre, tables de 0,9 m
	var x: float = r["x"]
	var z: float = r["z"]
	var col: Color = REFUGE_COL.get(r["id"], Color("2fbf71"))
	var g := h_at(x, z)
	var p := Node3D.new()
	p.position = Vector3(x, g, z)
	add_child(p)
	cyl(p, 6.0, 6.2, 1.2, mat(Color("cfc8b8"), 0.9), Vector3(0, 0.6, 0), 32)
	var rim := MeshInstance3D.new()
	var tm := TorusMesh.new()
	tm.inner_radius = 5.7
	tm.outer_radius = 6.05
	tm.rings = 48
	rim.mesh = tm
	rim.material_override = mat(col, 0.3, 0.0, 1.2)
	rim.position = Vector3(0, 1.22, 0)
	p.add_child(rim)
	refuge_rims[r["id"]] = rim
	for a in [0.7, 2.6, 4.4]:
		var tp := Vector3(cos(a) * 3.2, 1.2, sin(a) * 3.2)
		cyl(p, 0.45, 0.45, 0.06, mat(Color("8a6a4a")), tp + Vector3(0, 0.75, 0), 12)
		cyl(p, 0.06, 0.06, 0.75, mat(Color("4a3a2a")), tp + Vector3(0, 0.37, 0), 6)
	var l := text3d(p, "%s\n0 / %d" % [r["name"], r["cap"]], Vector3(0, 5.0, 0), 44, col.lightened(0.4))
	refuge_labels[r["id"]] = l
	var slots := []
	var cap: int = r["cap"]
	for i in cap:                                     # capacité lisible sans texte : une lanterne par place
		var a := i * TAU / cap - PI * 0.5
		cyl(p, 0.06, 0.06, 1.1, mat(Color("2f3a44"), 0.4, 0.6), Vector3(cos(a) * 6.6, 0.55, sin(a) * 6.6), 6)
		slots.append(sph(p, 0.32, mat(Color("3a4048"), 0.5), Vector3(cos(a) * 6.6, 1.3, sin(a) * 6.6)))
	refuge_slots[r["id"]] = [slots, col]
	block(x - 6.5, z - 6.5, x + 6.5, z + 6.5)

func set_refuge(id: String, n: int, cap: int, name_: String) -> void:
	if refuge_labels.has(id):
		refuge_labels[id].text = "%s\n%d / %d%s" % [name_, n, cap, "  COMPLET" if n >= cap else ""]
	if refuge_slots.has(id):
		var slots: Array = refuge_slots[id][0]
		var col: Color = refuge_slots[id][1]
		var full := n >= cap
		for i in slots.size():
			slots[i].material_override = mat(Color("ff4d5e") if full else col, 0.3, 0.0, 2.2) if i < n else mat(Color("3a4048"), 0.5)
		if refuge_rims.has(id):
			refuge_rims[id].material_override = mat(Color("ff4d5e") if full else col, 0.3, 0.0, 1.2)

func _house(x: float, z: float, ang: float, tag := "", outside := false) -> void:
	var p := Node3D.new()
	p.position = Vector3(x, h_ext(x, z) if outside else h_at(x, z), z)
	p.rotation.y = ang
	add_child(p)
	var walls := [Color("e9dcc9"), Color("d8c3a5"), Color("c9d6df"), Color("e4cfb0"), Color("d6d0c4"), Color("f0e2d0"), Color("bfc9b5")]
	var roofs := [Color("8b3a2b"), Color("6b4436"), Color("4f5b66"), Color("9a4b32")]
	box(p, Vector3(8, 0.5, 7), mat(Color("8b8378")), Vector3(0, 0.25, 0))
	box(p, Vector3(8, 4.2, 7), mat(walls[rng.randi() % walls.size()]), Vector3(0, 2.6, 0))
	var roof := MeshInstance3D.new()
	var pm := PrismMesh.new()
	pm.size = Vector3(8.6, 1.8, 7.6)
	roof.mesh = pm
	roof.material_override = mat(roofs[rng.randi() % roofs.size()], 0.8)
	roof.position = Vector3(0, 5.6, 0)
	p.add_child(roof)
	box(p, Vector3(0.6, 1.4, 0.6), mat(Color("7a6a5a")), Vector3(2.2, 6.0, 1.0))
	box(p, Vector3(1.1, 2.1, 0.08), mat(Color("5b3a29")), Vector3(0, 1.55, -3.52))
	for i in 2:
		for s in [-1, 1]:
			var lit := rng.randf() < 0.5
			box(p, Vector3(1.1, 1.0, 0.08), mat(Color("ffd9a0"), 0.2, 0.0, 0.5) if lit else mat(Color("2a3340"), 0.15, 0.3), Vector3(s * 2.4, 1.8 + i * 2.0, -3.52))
	if tag != "":
		text3d(p, tag, Vector3(0, 7.2, 0), 26, Color(1, 1, 1, 0.55))

func _houses(agents: Array) -> void:
	for a in agents:                               # la maison de chaque habitant, derrière sa porte, face à sa rue
		var hx: float = a["x"]
		var hz: float = a["z"]
		var e = nodes.get("E_" + a["id"])
		var dir := Vector2(0, -1)
		if e != null:
			dir = (Vector2(e[0], e[1]) - Vector2(hx, hz))
			dir = dir.normalized() if dir.length() > 0.1 else Vector2(0, -1)
		var c := Vector2(hx, hz) - dir * 3.9
		var ang := atan2(-dir.x, -dir.y)
		var ex: float = 4.0 if abs(dir.y) > 0.5 else 3.5
		var ez: float = 3.5 if abs(dir.y) > 0.5 else 4.0
		if free_spot(c.x - ex, c.y - ez, c.x + ex, c.y + ez, 0.0) and abs(c.x) < MX - 2 and abs(c.y) < MZ - 1:
			_house(c.x, c.y, ang, a["id"])
			block(c.x - ex, c.y - ez, c.x + ex, c.y + ez)
	for e in edges:                                # le reste du quartier : des maisons alignées face aux rues
		if e[3] in ["Pont Central", "Passage des Hauteurs", "allée"] or "parvis" in e[3]:
			continue
		var a := Vector2(nodes[e[0]][0], nodes[e[0]][1])
		var b := Vector2(nodes[e[1]][0], nodes[e[1]][1])
		var L := a.distance_to(b)
		if L < 9.0:
			continue
		var dir: Vector2 = (b - a) / L
		var nrm := Vector2(-dir.y, dir.x)
		var off: float = e[2] * 0.5 + (1.5 if e[2] >= 6.0 else 0.3) + 0.5 + 3.3
		for side in [-1.0, 1.0]:
			var k := 4.0
			while k < L - 4.0:
				var c: Vector2 = a + dir * k + nrm * off * side
				var face: Vector2 = -nrm * side                     # la porte regarde la rue
				var ang := atan2(-face.x, -face.y)
				var hx: float = 4.1 if abs(face.y) > 0.5 else 3.3
				var hz: float = 3.3 if abs(face.y) > 0.5 else 4.1
				if abs(c.x) < MX - 4 and abs(c.y) < MZ - 2 and free_spot(c.x - hx, c.y - hz, c.x + hx, c.y + hz, 0.3):
					_house(c.x, c.y, ang)
					block(c.x - hx, c.y - hz, c.x + hx, c.y + hz)
					k += 9.0
				else:
					k += 1.0

func _foliage_mat(c: Color) -> ShaderMaterial:
	var m := ShaderMaterial.new()
	m.shader = load("res://shaders/foliage.gdshader")
	m.set_shader_parameter("color", c)
	m.set_shader_parameter("grain", noise_c)
	foliage_mats.append(m)
	return m

func _trees() -> void:
	var greens := [_foliage_mat(Color("3f6b2f")), _foliage_mat(Color("4d7a35")), _foliage_mat(Color("355c2a")), _foliage_mat(Color("5c8a3a"))]
	var bark := mat(Color("5a4330"))
	var placed := 0
	var tries := 0
	while placed < int(170 * MX * MZ / 2400.0) and tries < int(4000 * MX * MZ / 2400.0):
		tries += 1
		var x := rng.randf_range(-MX + 2, MX - 2)
		var z := rng.randf_range(-MZ + 1, MZ - 1)
		if abs(z - RZ) < 8.5:
			continue
		if not free_spot(x - 1.5, z - 1.5, x + 1.5, z + 1.5, 0.2):
			continue
		var t := Node3D.new()
		t.position = Vector3(x, h_at(x, z), z)
		t.scale = Vector3.ONE * rng.randf_range(0.85, 1.35)
		add_child(t)
		cyl(t, 0.18, 0.225, 2.6, bark, Vector3(0, 1.3, 0), 7)            # tronc de 0,45 m
		var g: ShaderMaterial = greens[rng.randi() % greens.size()]
		for i in 3:                                                      # couronne de 3 m, hauteur totale 5 m
			var o := Vector3(rng.randf_range(-0.5, 0.5), 3.3 + i * 0.45, rng.randf_range(-0.5, 0.5))
			var s := sph(t, rng.randf_range(0.9, 1.5), g, o)
			s.scale = Vector3(1, 0.85, 1)
		block(x - 1.2, z - 1.2, x + 1.2, z + 1.2)
		placed += 1
	placed = 0
	tries = 0
	while placed < int(140 * MX * MZ / 2400.0) and tries < int(3000 * MX * MZ / 2400.0):        # arbustes de 1,2 m et roseaux des berges
		tries += 1
		var x := rng.randf_range(-MX + 2, MX - 2)
		var z := rng.randf_range(-MZ + 1, MZ - 1)
		var bankside: bool = abs(z - RZ) > 6.5 and abs(z - RZ) < 9.5
		if abs(z - RZ) < 6.5 or not free_spot(x - 0.7, z - 0.7, x + 0.7, z + 0.7, 0.0):
			continue
		if bankside:
			for i in 5:
				var r := cyl(self, 0.02, 0.03, rng.randf_range(0.8, 1.6), mat(Color("7f8f4a")), Vector3(x + rng.randf_range(-0.5, 0.5), h_at(x, z) + 0.5, z + rng.randf_range(-0.4, 0.4)), 4)
				r.rotation.z = rng.randf_range(-0.2, 0.2)
		else:
			var b := sph(self, 0.6, greens[rng.randi() % greens.size()], Vector3(x, h_at(x, z) + 0.45, z))
			b.scale = Vector3(1, 0.75, 1)
		placed += 1
	for i in int(MX):                                                      # rochers le long de la rivière
		var x := rng.randf_range(-MX + 2, MX - 2)
		var z := RZ + (7.2 if rng.randf() < 0.5 else -7.2) + rng.randf_range(-0.6, 0.6)
		if abs(x) < 4 or abs(x - PX) < 3.5:
			continue
		var r := sph(self, rng.randf_range(0.25, 0.7), mat(Color("77746c"), 0.95), Vector3(x, h_at(x, z) - 0.1, z))
		r.scale = Vector3(1.3, 0.6, 1.0)

func _street_furniture() -> void:
	var pole := mat(Color("2f3a44"), 0.4, 0.6)
	var n := 0
	for e in edges:
		if e[2] < 6.0 or e[3] in ["Pont Central", "Passage des Hauteurs"] or "parvis" in e[3]:
			continue
		var a := Vector2(nodes[e[0]][0], nodes[e[0]][1])
		var b := Vector2(nodes[e[1]][0], nodes[e[1]][1])
		var L := a.distance_to(b)
		var dir: Vector2 = (b - a) / max(L, 0.01)
		var nrm := Vector2(-dir.y, dir.x) * 4.6
		var k := 7.0
		while k < L - 3.0:                                                # lampadaires de 5 m
			var p: Vector2 = a + dir * k + nrm * (1.0 if n % 2 == 0 else -1.0)
			var y := h_at(p.x, p.y)
			cyl(self, 0.15, 0.15, 0.3, pole, Vector3(p.x, y + 0.15, p.y), 8)
			cyl(self, 0.045, 0.045, 5.0, pole, Vector3(p.x, y + 2.5, p.y), 6)
			var lan := sph(self, 0.175, mat(Color("ffd89a"), 0.3, 0.0, 2.0), Vector3(p.x, y + 5.0, p.y))
			lamps.append(lan)
			if n % 3 == 0:
				var l := OmniLight3D.new()
				l.light_color = Color("ffc98a")
				l.omni_range = 11.0
				l.light_energy = 0.0
				l.position = Vector3(p.x, y + 4.8, p.y)
				add_child(l)
				lamps.append(l)
			n += 1
			k += 15.0
	# panneaux de direction (poteau 0,16 m, panneau 2,4 x 0,7 m)
	var signs := [[Vector2(4, 9 * S + 2), "→ Gymnase par le Pont Central"], [Vector2(PX + 3, 9 * S + 2), "↑ Passage des Hauteurs (escaliers)"],
		[Vector2(-46 * S, 20 * S), "← École des Tilleuls (refuge)"], [Vector2(4, -16 * S + 3), "→ Gymnase des Hauteurs"]]
	for s in signs:
		var p: Vector2 = s[0]
		var y := h_at(p.x, p.y)
		cyl(self, 0.08, 0.08, 3.0, pole, Vector3(p.x, y + 1.5, p.y), 6)
		box(self, Vector3(2.4, 0.7, 0.06), mat(Color("1f6f43")), Vector3(p.x, y + 2.7, p.y))
		var l := text3d(self, s[1], Vector3(p.x, y + 2.7, p.y - 0.05), 20, Color.WHITE, false)
		l.pixel_size = 0.006
		l.rotation.y = PI
	for i in 14:                                                          # bouches d'égout (0,6 m)
		var e = edges[rng.randi() % edges.size()]
		var a := Vector2(nodes[e[0]][0], nodes[e[0]][1])
		var b := Vector2(nodes[e[1]][0], nodes[e[1]][1])
		var p := a.lerp(b, rng.randf())
		if e[3] in ["Pont Central", "Passage des Hauteurs"]:
			continue
		cyl(self, 0.3, 0.3, 0.03, mat(Color("2a2a2a"), 0.5, 0.7), Vector3(p.x, h_at(p.x, p.y) + 0.15, p.y), 12)

func set_night(storm: float) -> void:
	for l in lamps:
		if l is OmniLight3D:
			l.light_energy = 0.6 + storm * 0.8

func pulse(t: float, bulletin: bool) -> void:
	if siren:
		siren.light_energy = (6.0 * abs(sin(t * 7.0))) if bulletin else 0.0
	if barrier_light and barrier and barrier.visible:
		barrier_light.light_energy = 3.0 * abs(sin(t * 5.0))

# ---------- monde agrandi : faubourgs, routes et bois autour du quartier simulé (décor, les habitants restent dans le quartier) ----------
func _street_end(z: float, west: bool) -> float:   # x du bout ouest (ou est) d'une rue simulée à la hauteur z
	var best := 0.0
	for k in nodes:
		var n: String = k
		if n.begins_with("E_") or n.begins_with("H_A") or abs(float(nodes[k][1]) - z) > 0.5:
			continue
		var x: float = nodes[k][0]
		best = min(best, x) if west else max(best, x)
	return best

func _outskirts() -> void:
	var asphalt := mat(Color("3a3d42"), 0.75)
	var qz := 9.0 * S                                  # les rues du quartier se prolongent dans les faubourgs
	var hz := -16.0 * S
	var sz := 37.0 * S
	var extended := MX > 61.0 * S                      # MONDE=etendu : la rue Haute côté est laisse la place à la rue de la Berge et au collège
	var roads := [[Vector2(_street_end(qz, true) - 3, qz), Vector2(-OUT_X + 6, qz)], [Vector2(_street_end(qz, false) + 3, qz), Vector2(OUT_X - 6, qz)],
		[Vector2(_street_end(hz, true) - 3, hz), Vector2(-OUT_X + 6, hz)], [Vector2((MX if extended else _street_end(hz, false)) + 3, hz), Vector2(OUT_X - 6, hz)],
		[Vector2(_street_end(sz, true) - 3, sz), Vector2(-OUT_X + 6, sz)], [Vector2(_street_end(sz, false) + 3, sz), Vector2(OUT_X - 6, sz)],
		[Vector2(0, sz + 3), Vector2(0, OUT_Z - 6)], [Vector2(0, hz - 3), Vector2(0, -OUT_Z + 6)],
		[Vector2(MX + 20, hz), Vector2(MX + 20, -OUT_Z + 6)], [Vector2(-MX - 30, qz), Vector2(-MX - 30, OUT_Z - 6)], [Vector2(MX + 50, qz), Vector2(MX + 50, OUT_Z - 6)]]
	for rd in roads:
		var a: Vector2 = rd[0]
		var b: Vector2 = rd[1]
		var L := a.distance_to(b)
		var dir := (b - a) / L
		var n := int(ceil(L / 5.0))
		for i in n:
			var p0 := a + dir * (L * i / n)
			var p1 := a + dir * (L * (i + 1) / n)
			var mid := (p0 + p1) * 0.5
			var y: float = max(h_ext(p0.x, p0.y), h_ext(p1.x, p1.y), h_ext(mid.x, mid.y))
			box(self, Vector3(5.0, 0.12, L / n + 0.6), asphalt, Vector3(mid.x, y + 0.02, mid.y), atan2(dir.x, dir.y))
		block(min(a.x, b.x) - 3.5, min(a.y, b.y) - 3.5, max(a.x, b.x) + 3.5, max(a.y, b.y) + 3.5)
	for rd in roads:                                   # maisons des faubourgs, de plus en plus espacées vers la campagne
		var a: Vector2 = rd[0]
		var b: Vector2 = rd[1]
		var L := a.distance_to(b)
		var dir := (b - a) / L
		var nrm := Vector2(-dir.y, dir.x)
		for side in [-1.0, 1.0]:
			var k := 8.0
			while k < L - 6.0:
				var c: Vector2 = a + dir * k + nrm * 9.0 * side
				var far: float = max(abs(c.x) / OUT_X, abs(c.y) / OUT_Z)
				if far < 0.85 and abs(c.y - RZ) > 14.0 and rng.randf() > far * 0.8 and free_spot(c.x - 4.5, c.y - 4.5, c.x + 4.5, c.y + 4.5, 0.5):
					var face: Vector2 = -nrm * side
					_house(c.x, c.y, atan2(-face.x, -face.y), "", true)
					block(c.x - 4.5, c.y - 4.5, c.x + 4.5, c.y + 4.5)
				k += rng.randf_range(10.0, 16.0)
	var fm := [_foliage_mat(Color("2f5526")), _foliage_mat(Color("3f6b2f")), _foliage_mat(Color("4d7a35"))]
	var bark := mat(Color("4a3a2a"))
	var placed := 0
	var tries := 0
	while placed < 600 and tries < 9000:               # bois et bosquets autour du quartier
		tries += 1
		var x := rng.randf_range(-OUT_X + 4, OUT_X - 4)
		var z := rng.randf_range(-OUT_Z + 4, OUT_Z - 4)
		if (abs(x) < MX + 3 and abs(z) < MZ + 3) or abs(z - RZ) < 11:
			continue
		if not free_spot(x - 1.5, z - 1.5, x + 1.5, z + 1.5, 0.2):
			continue
		var t := Node3D.new()
		t.position = Vector3(x, h_ext(x, z), z)
		t.scale = Vector3.ONE * rng.randf_range(0.9, 1.6)
		add_child(t)
		if rng.randf() < 0.5:
			var hh := rng.randf_range(6, 11)
			cyl(t, 0.15, 0.3, hh * 0.4, bark, Vector3(0, hh * 0.2, 0), 6)
			var cone := cyl(t, 0.0, rng.randf_range(1.6, 2.8), hh * 0.8, fm[0], Vector3(0, hh * 0.65, 0), 8)
			cone.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		else:
			cyl(t, 0.18, 0.225, 2.6, bark, Vector3(0, 1.3, 0), 6)
			var s := sph(t, rng.randf_range(1.3, 2.0), fm[1 + rng.randi() % 2], Vector3(0, 3.6, 0))
			s.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		block(x - 1.2, z - 1.2, x + 1.2, z + 1.2)
		placed += 1
