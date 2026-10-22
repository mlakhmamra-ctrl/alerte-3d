extends Node3D
# ALERTE 3D — client Godot 4.3+. Reçoit les événements de la simulation par la passerelle (Kafka -> WebSocket)
# et envoie les commandes de l'observateur (alerte, rumeur, fermeture du pont, pause, relance).
# La scène ne décide rien : elle montre ce que la simulation publie, synchronisé sur l'horloge simulée.

const URL := "ws://127.0.0.1:8765"
const AgentView := preload("res://agent_view.gd")
const WorldBuilder := preload("res://world_builder.gd")
const KIND_COL := {"officiel": Color("4da3ff"), "rumeur": Color("ff4d5e"), "relais": Color("f5f5f5"), "observation": Color("ffd23f"),
	"correction": Color("3ddc84"), "message": Color("53d6ff"), "verification": Color("b48cff")}
const KIND_FR := {"officiel": "bulletin officiel", "rumeur": "rumeur", "relais": "relais", "observation": "observation",
	"correction": "correction", "message": "message", "verification": "vérification"}
const ACT_FR := {"rester": "reste", "preparer_depart": "prépare son départ", "partir": "part", "changer_itineraire": "change d'itinéraire",
	"relayer": "relaie", "verifier": "vérifie", "corriger": "corrige"}

var ws := WebSocketPeer.new()
var retry := 0.0
var world: Node3D
var agents := {}
var ainfo := {}
var journal := {}
var beliefs := {}
var last_think := {}
var cam := Camera3D.new()
var yaw := 0.55
var pitch := 0.72
var dist := 120.0
var cam_attr: CameraAttributesPractical
var focus := Vector3(-4, 0, 4)
var dragging := 0
var sel := ""
var follow := false
var cine := false
var show_cells := true
var t_sim := 540.0
var t_end := 600.0
var level := -1.35
var level_shown := -1.35
var storm := 0.0
var started := false
var run_info := {}
var truth := {"bridge": "ouvert", "danger": false, "refuges": []}
var ind := {}
var metrics := {}
var hist := []
var pulses := []
var waves := []
var bulletin_t := 0.0
var replay := false
var paused := false
var tick_dt := 1.0
var last_tick_ms := 0
var refuge_names := {}
var rain: CPUParticles3D
var rain_mat: StandardMaterial3D
var env: Environment
var sun: DirectionalLight3D
var sky_mat: ProceduralSkyMaterial
var ring: MeshInstance3D
# interface
var ui := CanvasLayer.new()         # vue par défaut : uniquement des repères visuels, aucun texte (consigne du cours)
var txt := CanvasLayer.new()        # panneaux de texte À CÔTÉ de la scène, comme dans la vidéo AGORA (touche I : masquer)
var vis := Control.new()
var show_text := true               # panneaux latéraux ; la scène 3D elle-même reste toujours sans texte
var left_panel: PanelContainer
var right_panel: PanelContainer
var feed_panel: PanelContainer
var title_panel: PanelContainer
var title_lbl := RichTextLabel.new()
var feed_lbl := RichTextLabel.new()
var cards_box := VBoxContainer.new()
var cards := {}
var feed := []
var clock_lbl := Label.new()
var status_lbl := Label.new()
var banner := RichTextLabel.new()
var banner_panel: PanelContainer
var ind_lbl := RichTextLabel.new()
var insp := RichTextLabel.new()
var insp_panel: PanelContainer
var chart := Control.new()
var lines := {}
var help_lbl := Label.new()
var waiting := Label.new()
var btns := {}
var shot := {}          # capture d'écran automatique : godot --path . -- --shot=img.png --after=20 [--select=A04] [--cam=x,y,z,dist,yaw,pitch]

func _ready() -> void:
	_environment()
	world = WorldBuilder.new()
	add_child(world)
	cam.fov = 38.0                      # focale longue : vue de maquette (vidéo AGORA)
	cam.far = 1600.0
	cam_attr = CameraAttributesPractical.new()     # flou « tilt-shift » optionnel (touche P), coupé par défaut : image nette
	cam_attr.dof_blur_far_enabled = false
	cam_attr.dof_blur_near_enabled = false
	cam_attr.dof_blur_amount = 0.09
	cam.attributes = cam_attr
	add_child(cam)
	var tm := TorusMesh.new()
	tm.inner_radius = 0.52
	tm.outer_radius = 0.6        # anneau de sélection : 1,20 m, épaisseur 0,08 m
	ring = MeshInstance3D.new()
	ring.mesh = tm
	var rm := StandardMaterial3D.new()
	rm.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	rm.albedo_color = Color("ffe08a")
	ring.material_override = rm
	ring.visible = false
	add_child(ring)
	_ui()
	ws.inbound_buffer_size = 16 * 1024 * 1024
	ws.connect_to_url(URL)
	for arg in OS.get_cmdline_user_args():
		var kv := arg.trim_prefix("--").split("=")
		if kv.size() == 2:
			shot[kv[0]] = kv[1]
	if shot.has("cam"):
		var c: PackedStringArray = str(shot["cam"]).split(",")
		focus = Vector3(float(c[0]), float(c[1]), float(c[2]))
		dist = float(c[3])
		yaw = float(c[4])
		pitch = float(c[5])

const SKY_TOP := Color("6d6aa8")
const SKY_HOR := Color("f0a77c")
func _environment() -> void:
	sky_mat = ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = SKY_TOP                 # ciel de fin de journée, rose et violet, comme dans la vidéo
	sky_mat.sky_horizon_color = SKY_HOR
	sky_mat.ground_horizon_color = SKY_HOR
	sky_mat.ground_bottom_color = Color("4a3550")
	sky_mat.sun_angle_max = 20.0
	var sky := Sky.new()
	sky.sky_material = sky_mat
	env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.9
	env.ambient_light_sky_contribution = 0.55        # le ciel rose ne teinte pas tout : l'herbe reste verte
	env.ambient_light_color = Color("c8ccd8")
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.tonemap_exposure = 1.05
	env.ssao_enabled = true
	env.ssao_radius = 1.4
	env.glow_enabled = true
	env.glow_intensity = 0.35
	env.glow_bloom = 0.0
	env.glow_hdr_threshold = 1.2
	env.fog_enabled = true
	env.fog_light_color = Color("c9a6b4")
	env.fog_density = 0.0015
	env.fog_aerial_perspective = 0.0
	env.fog_sky_affect = 0.2
	env.adjustment_enabled = true
	env.adjustment_saturation = 1.12
	env.adjustment_contrast = 1.15
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)
	sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-32, -35, 0)      # soleil bas et chaud : lumière dorée, longues ombres
	sun.light_energy = 1.35
	sun.light_color = Color("ffd6a8")
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 260.0
	add_child(sun)
	rain = CPUParticles3D.new()                     # pluie qui suit la caméra, plus dense quand la crue monte
	rain.amount = 3500
	rain.lifetime = 1.1
	rain.preprocess = 1.0
	rain.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	rain.emission_box_extents = Vector3(60, 2, 60)
	rain.direction = Vector3(0.15, -1, 0.05)
	rain.spread = 2.0
	rain.gravity = Vector3(0, -30, 0)
	rain.initial_velocity_min = 22.0
	rain.initial_velocity_max = 28.0
	var dm := BoxMesh.new()
	dm.size = Vector3(0.02, 0.5, 0.02)
	rain.mesh = dm
	var rmat := StandardMaterial3D.new()
	rmat.albedo_color = Color(0.75, 0.8, 0.88, 0.35)
	rmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	rmat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	rain.material_override = rmat
	rain_mat = rmat
	rain.local_coords = false
	rain.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(rain)

# ---------- interface ----------
func _panel(pos: Vector2, size: Vector2, alpha := 0.78) -> PanelContainer:
	var p := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.06, 0.08, 0.11, alpha)
	sb.set_corner_radius_all(10)
	sb.set_content_margin_all(12)
	sb.border_color = Color(1, 1, 1, 0.08)
	sb.set_border_width_all(1)
	p.add_theme_stylebox_override("panel", sb)
	p.position = pos
	p.size = size
	txt.add_child(p)
	return p

func _rt(r: RichTextLabel, fs := 15) -> void:
	r.bbcode_enabled = true
	r.fit_content = true
	r.scroll_active = false
	r.add_theme_font_size_override("normal_font_size", fs)
	r.add_theme_font_size_override("bold_font_size", fs)
	r.add_theme_font_size_override("italics_font_size", fs)
	r.mouse_filter = Control.MOUSE_FILTER_IGNORE

func _ui() -> void:
	add_child(ui)
	add_child(txt)
	txt.visible = show_text
	vis.set_anchors_preset(Control.PRESET_FULL_RECT)
	vis.mouse_filter = Control.MOUSE_FILTER_IGNORE
	vis.draw.connect(_draw_vis)
	ui.add_child(vis)
	# colonne de gauche : une fiche par habitant (identifiant, couleur de casquette, état, risque, croyances)
	left_panel = _panel(Vector2(12, 14), Vector2(300, 860), 0.82)
	var lv := VBoxContainer.new()
	lv.add_theme_constant_override("separation", 4)
	left_panel.add_child(lv)
	clock_lbl.add_theme_font_size_override("font_size", 20)
	clock_lbl.text = "ALERTE · Rive-Claire"
	lv.add_child(clock_lbl)
	status_lbl.add_theme_font_size_override("font_size", 11)
	status_lbl.modulate = Color(1, 1, 1, 0.7)
	status_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_lbl.custom_minimum_size = Vector2(276, 0)
	lv.add_child(status_lbl)
	var sc := ScrollContainer.new()
	sc.custom_minimum_size = Vector2(276, 0)
	sc.size_flags_vertical = Control.SIZE_EXPAND_FILL
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	lv.add_child(sc)
	cards_box.add_theme_constant_override("separation", 5)
	cards_box.custom_minimum_size = Vector2(270, 0)
	sc.add_child(cards_box)
	# titre en haut au centre : la scène en cours et le décompte
	title_panel = _panel(Vector2(560, 14), Vector2(480, 0), 0.7)
	_rt(title_lbl, 14)
	title_lbl.custom_minimum_size = Vector2(456, 0)
	title_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title_panel.add_child(title_lbl)
	# fil en direct en bas : les derniers messages échangés, mot pour mot
	feed_panel = _panel(Vector2(324, 760), Vector2(620, 0), 0.8)
	_rt(feed_lbl, 12)
	feed_lbl.custom_minimum_size = Vector2(596, 0)
	feed_panel.add_child(feed_lbl)
	banner.visible = false
	# colonne de droite : indicateurs, courbes, commandes
	right_panel = _panel(Vector2(1248, 14), Vector2(340, 0), 0.82)
	var rv := VBoxContainer.new()
	rv.add_theme_constant_override("separation", 6)
	right_panel.add_child(rv)
	_rt(ind_lbl, 12)
	ind_lbl.custom_minimum_size = Vector2(316, 0)
	ind_lbl.text = "[b]Indicateurs[/b]\n[color=#8899aa]En attente des indicateurs calculés par la passerelle…[/color]"
	rv.add_child(ind_lbl)
	chart.custom_minimum_size = Vector2(316, 100)
	chart.mouse_filter = Control.MOUSE_FILTER_IGNORE
	chart.draw.connect(_draw_chart)
	rv.add_child(chart)
	var leg := Label.new()
	leg.add_theme_font_size_override("font_size", 11)
	leg.text = "— contexte commun   — convergence refuge   — à l'abri"
	leg.modulate = Color(1, 1, 1, 0.6)
	rv.add_child(leg)
	var head := Label.new()
	head.add_theme_font_size_override("font_size", 11)
	head.text = "SCÉNARIOS ET COMMANDES"
	head.modulate = Color(1, 1, 1, 0.55)
	rv.add_child(head)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 6)
	rv.add_child(grid)
	for b in [["alert", "Alerte officielle (A)", "4da3ff"], ["rumor", "Rumeur (U)", "ff4d5e"], ["bridge", "Fermer le pont (B)", "ff9f1c"], ["pause", "Pause (Espace)", "f4f1e8"],
			["mode", "Sync/Async (M)", "b48cff"], ["scen", "Scénario (N)", "53d6ff"], ["restart", "Relancer (R)", "3ddc84"], ["cells", "Cellules (G)", "ffd23f"],
			["cine", "Ciné (C)", "f4f1e8"], ["follow", "Suivre (F)", "f4f1e8"]]:
		var bt := Button.new()
		bt.text = "●  " + b[1]
		bt.alignment = HORIZONTAL_ALIGNMENT_LEFT
		bt.focus_mode = Control.FOCUS_NONE
		bt.custom_minimum_size = Vector2(155, 30)
		bt.add_theme_font_size_override("font_size", 12)
		bt.add_theme_color_override("font_color", Color(b[2]).lerp(Color.WHITE, 0.35))
		var sb := StyleBoxFlat.new()
		sb.bg_color = Color(1, 1, 1, 0.06)
		sb.set_corner_radius_all(6)
		sb.set_content_margin_all(6)
		bt.add_theme_stylebox_override("normal", sb)
		var sh := sb.duplicate()
		sh.bg_color = Color(1, 1, 1, 0.14)
		bt.add_theme_stylebox_override("hover", sh)
		bt.add_theme_stylebox_override("pressed", sh)
		bt.pressed.connect(_on_button.bind(b[0]))
		grid.add_child(bt)
		btns[b[0]] = bt
	help_lbl.add_theme_font_size_override("font_size", 11)
	help_lbl.modulate = Color(1, 1, 1, 0.5)
	help_lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	help_lbl.custom_minimum_size = Vector2(316, 0)
	help_lbl.text = "Clic sur un habitant ou sa fiche : sa mémoire · clic droit + glisser : tourner · molette : zoom · ZQSD/WASD : déplacer · T : vue du dessus · Tab : habitant suivant · P : flou miniature · I : masquer les panneaux"
	rv.add_child(help_lbl)
	# fiche détaillée de l'habitant sélectionné, à droite de la colonne des habitants
	insp_panel = _panel(Vector2(324, 70), Vector2(400, 0), 0.86)
	_rt(insp, 12)
	insp.custom_minimum_size = Vector2(376, 0)
	insp_panel.add_child(insp)
	waiting.text = "En attente de la simulation…\nLancez « docker compose up » puis patientez (téléchargement des modèles au premier lancement)."
	waiting.add_theme_font_size_override("font_size", 20)
	waiting.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	waiting.position = Vector2(400, 470)
	waiting.size = Vector2(800, 100)
	txt.add_child(waiting)

func _card(info: Dictionary, col: Color) -> void:
	var bt := Button.new()                           # fiche cliquable : sélectionne l'habitant
	bt.focus_mode = Control.FOCUS_NONE
	bt.custom_minimum_size = Vector2(270, 54)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(1, 1, 1, 0.05)
	sb.set_corner_radius_all(6)
	sb.border_color = col
	sb.border_width_left = 4
	bt.add_theme_stylebox_override("normal", sb)
	bt.add_theme_stylebox_override("hover", sb)
	bt.add_theme_stylebox_override("pressed", sb)
	var r := RichTextLabel.new()
	_rt(r, 11)
	r.position = Vector2(10, 4)
	r.size = Vector2(256, 48)
	r.fit_content = false
	bt.add_child(r)
	bt.pressed.connect(func(): sel = info["id"])
	cards_box.add_child(bt)
	cards[info["id"]] = [bt, r, sb]

func _on_button(id: String) -> void:
	match id:
		"alert": _send({"type": "inject_alert"})
		"rumor": _send({"type": "inject_rumor"})
		"bridge": _send({"type": "close_bridge"})
		"pause": _send({"type": "resume" if paused else "pause"})
		"mode": _send({"type": "set_mode", "mode": "sync" if run_info.get("mode", "async") == "async" else "async"})
		"scen": _send({"type": "set_scenario", "scenario": "pont_ouvert" if run_info.get("scenario", "") == "pont_ferme" else "pont_ferme"})
		"restart": _send({"type": "restart"})
		"cells":
			show_cells = not show_cells
			if world.cells:
				world.cells.visible = show_cells
		"cine": cine = not cine
		"follow": follow = not follow
		"text":
			show_text = not show_text
			txt.visible = show_text

func _send(d: Dictionary) -> void:
	if ws.get_ready_state() == WebSocketPeer.STATE_OPEN:
		ws.send_text(JSON.stringify(d))
		_banner("[color=#9fb0c0]Commande envoyée : %s[/color]" % d["type"])

func _banner(t: String) -> void:
	banner.text = t
	feed.append("[color=#8899aa]%s[/color]  %s" % [_hm(t_sim), t])
	if feed.size() > 4:
		feed.pop_front()
	feed_lbl.text = "[color=#8899aa]FIL EN DIRECT[/color]\n" + "\n".join(feed)

func _hm(t: float) -> String:
	t = max(0.0, t)
	return "%02d:%02d" % [int(t / 60.0), int(fmod(t, 60.0))]

func _name(id) -> String:
	if id == null:
		return "?"
	if str(id) == "mairie":
		return "la mairie"
	if str(id) == "anonyme":
		return "un anonyme"
	return "%s (%s)" % [ainfo.get(str(id), {}).get("name", str(id)), str(id)]

# ---------- réseau ----------
func _process(delta: float) -> void:
	ws.poll()
	var st := ws.get_ready_state()
	if st == WebSocketPeer.STATE_OPEN:
		var n := 0
		while ws.get_available_packet_count() > 0 and n < 400:
			_handle(ws.get_packet().get_string_from_utf8())
			n += 1
	elif st == WebSocketPeer.STATE_CLOSED:
		retry -= delta
		if retry <= 0:
			retry = 2.0
			ws = WebSocketPeer.new()
			ws.inbound_buffer_size = 16 * 1024 * 1024
			ws.connect_to_url(URL)
	waiting.visible = not started
	if shot.has("shot") and Time.get_ticks_msec() > float(shot.get("after", "20")) * 1000.0:
		get_viewport().get_texture().get_image().save_png(shot["shot"])
		print("capture : ", shot["shot"], " t_sim=", _hm(t_sim), " agents=", agents.size())
		get_tree().quit()
		shot.erase("shot")
	if shot.has("select") and agents.has(shot["select"]):
		sel = shot["select"]
	_animate(delta)
	_camera(delta)
	_hud()

func _handle(txt: String) -> void:
	var ev = JSON.parse_string(txt)
	if typeof(ev) != TYPE_DICTIONARY:
		return
	var ty: String = ev.get("type", "")
	var d: Dictionary = ev.get("data", {})
	var a = ev.get("agent")
	if ev.get("t_sim", -1) is float and ev["t_sim"] >= 0 and ty != "indicator":
		t_sim = max(t_sim, ev["t_sim"])
	replay = ev.get("replay", replay)
	match ty:
		"run.start": _start(d)
		"world.tick":
			var now := Time.get_ticks_msec()
			if last_tick_ms > 0:
				tick_dt = clamp((now - last_tick_ms) / 1000.0, 0.15, 3.0)
			last_tick_ms = now
			level = d["level"]
			world.set_bridge(d.get("bridge", true))
			for p in d["agents"]:
				var v = agents.get(p["id"])
				if v == null:
					continue
				var tgt := Vector3(p["x"], 0, p["z"])
				var old: Vector3 = v.target
				v.target = tgt
				v.vel = (tgt - old) / tick_dt
				v.depth = p.get("depth", 0.0)
				v.thinking = p.get("thinking", false)
				v.set_state(p["state"])
		"flood.update":
			level = d["level"]
			world.set_cells(d["cells"], d["level"])
		"world.truth": truth = d
		"bridge.closed":
			world.set_bridge(false)
			_banner("[color=#ff6b6b][b]Le Pont Central est fermé[/b][/color] (vérité du monde). Les habitants ne le sauront qu'en le voyant ou en l'apprenant.")
		"official.bulletin":
			bulletin_t = 4.0
			waves.append({"pos": world.poste_pos + Vector3(0, 2, 0), "t": 0.0, "col": KIND_COL["officiel"]})
			_banner("[color=#4da3ff][b]Mairie → tous[/b][/color]  « %s »" % d["text"])
			for id in d.get("to", []):
				_log(id, "[color=#4da3ff]bulletin[/color] de la mairie (%s)" % d["level"])
		"rumor":
			for id in d["targets"]:
				if agents.has(id):
					waves.append({"pos": agents[id].position + Vector3(0, 2, 0), "t": 0.0, "col": KIND_COL["rumeur"]})
					_log(id, "[color=#ff4d5e]anonyme[/color] : « %s »" % d["text"])
			_banner("[color=#ff4d5e][b]Rumeur anonyme → %s[/b][/color]  « %s »  [color=#9fb0c0](%s)[/color]" % [", ".join(d["targets"]), d["text"], "vraie" if d.get("true", false) else "fausse"])
		"agent.say": _say(str(a), d)
		"agent.think":
			last_think[a] = d
			_log(a, "[color=#c9a8ff]décide[/color] %s : %s%s" % [ACT_FR.get(d["action"], d["action"]), d["why"], ("  [%s]" % d.get("model", "")) if d.get("decider", "rule") != "rule" else ""])
		"agent.belief": beliefs[a] = d
		"agent.prepare": _log(a, "[color=#f2b134]prépare[/color] son départ (prêt à %s)" % _hm(d["ready_at"]))
		"agent.depart":
			_log(a, "[color=#f2b134]part[/color] vers %s par %s" % [refuge_names.get(d["refuge"], d["refuge"]), str(d["via"]).replace("_", " ")])
		"agent.reroute":
			_log(a, "[color=#ff9f1c]demi-tour[/color] (%s) → %s" % [d["reason"], refuge_names.get(d.get("refuge", ""), "?")])
		"agent.blocked": _log(a, "[color=#ff4d5e]bloqué[/color] : %s" % d["reason"])
		"agent.shelter": _log(a, "[color=#9b8cff]à l'abri chez soi, à l'étage[/color] : %s" % d["reason"])
		"agent.arrive":
			_log(a, "[color=#3ddc84]à l'abri[/color] : %s" % refuge_names.get(d["refuge"], d["refuge"]))
			if d.has("load") and d.has("cap"):
				world.set_refuge(d["refuge"], int(d["load"]), int(d["cap"]), refuge_names.get(d["refuge"], d["refuge"]))
		"agent.observe":
			var w: String = d["what"]
			_log(a, "[color=#ffd23f]voit[/color] %s" % ({"eau": "de l'eau (%d cm)" % int(d.get("depth", 0) * 100), "pont_ferme": "le pont fermé", "rue_inondee": "la rue inondée (%s)" % d.get("street", "")}.get(w, w)))
		"agent.verify": _log(a, "[color=#b48cff]vérifie[/color] appelle le poste municipal")
		"agent.verify_result": _log(a, "[color=#b48cff]réponse[/color] %s" % d["result"])
		"refuge.full": _log(a, "[color=#ff4d5e]refuge plein[/color] : %s" % refuge_names.get(d["refuge"], d["refuge"]))
		"llm.incident": _log(a, "[color=#ff4d5e]incident LLM[/color] : %s" % d["kind"])
		"indicator": ind = d; _push_hist()
		"metrics": metrics = d
		"run.end":
			metrics = d
			_banner("[b]Fin du run[/b] · %d/%d à l'abri · contexte commun moyen %.0f %% · %d appels LLM. Appuyez sur R pour relancer." % [d["indicators"]["safe"], run_info.get("n_agents", 12), d["indicators"].get("common_context_avg", 0) * 100, d.get("calls_total", 0)])
		"run.paused": paused = true
		"run.resumed": paused = false
		"command.ack":
			if d.get("cmd") == "pause":
				paused = true
			elif d.get("cmd") == "resume":
				paused = false

func _start(d: Dictionary) -> void:
	run_info = d
	started = true
	paused = false
	t_sim = d["t_start"]
	t_end = d["t_end"]
	hist.clear()
	journal.clear()
	beliefs.clear()
	last_think.clear()
	pulses.clear()
	for v in agents.values():
		v.queue_free()
	agents.clear()
	ainfo.clear()
	world.build(d)
	world.set_labels_visible(false)                  # la scène 3D reste sans texte ; le texte est dans les panneaux
	for c in cards.values():
		c[0].queue_free()
	cards.clear()
	if not shot.has("cam"):                           # cadrage initial adapté à la taille du quartier (WORLD_SCALE)
		focus = Vector3(-4, 0, 4) * world.S
		dist = 120.0 * sqrt(world.MX / 60.0)
	var order: Array = d["agents"].map(func(x): return x["id"])
	order.sort()
	world.cells.visible = show_cells
	for r in d["refuges"]:
		refuge_names[r["id"]] = r["name"]
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	var skins := [Color("f1c7a5"), Color("d9a47c"), Color("a8714f"), Color("7a4b31"), Color("e8b892")]
	var hairs := [Color("2b1d14"), Color("4a3020"), Color("111111"), Color("6b4a2a"), Color("8a8a8a")]
	var cloth := [Color("3b5b92"), Color("8f3b3b"), Color("3f7d5a"), Color("6a4f8f"), Color("9a6b2f"), Color("2f6f7a"), Color("555b66")]
	for info in d["agents"]:
		var v = AgentView.new()
		add_child(v)
		v.setup(info, skins[rng.randi() % skins.size()], hairs[rng.randi() % hairs.size()], cloth[rng.randi() % cloth.size()])
		v.set_id_color(Color.from_hsv(float(order.find(info["id"])) / max(1, order.size()), 0.7, 0.95))
		_card(info, v.id_col)
		v.position = Vector3(info["x"], world.walk_y(info["x"], info["z"]), info["z"])
		v.target = v.position
		agents[info["id"]] = v
		ainfo[info["id"]] = info
		journal[info["id"]] = []
	_banner("[b]Nouveau run[/b] %s · scénario %s · mode %s · %d habitants (%s pensent avec un LLM)%s" % [str(d.get("seed", "")), d["scenario"], d["mode"], d["n_agents"], ", ".join(d.get("llm_agents", [])), "  [color=#ffd23f]REJEU (aucun appel LLM)[/color]" if replay else ""])

func _log(a, s: String) -> void:
	if a == null or not journal.has(str(a)):
		return
	var j: Array = journal[str(a)]
	j.append("[color=#8899aa]%s[/color] %s" % [_hm(t_sim), s])
	if j.size() > 40:
		j.pop_front()

func _say(a: String, d: Dictionary) -> void:
	var col: Color = KIND_COL.get(d.get("kind", "message"), Color.WHITE)
	var to: Array = d.get("to", [])
	for r in to:
		if agents.has(a) and agents.has(r):
			pulses.append({"from": agents[a], "to": agents[r], "t": 0.0, "dur": 1.4, "col": col})
		_log(r, "[color=#53d6ff]reçoit[/color] de %s : « %s »" % [_name(a), d["text"]])
	_log(a, "[color=#53d6ff]écrit[/color] à %s : « %s »" % [", ".join(to), d["text"]])
	if agents.has(a):
		for v in agents.values():
			v.bubble.visible = false
			v.bubble_t = 0.0
		agents[a].say("« %s »" % d["text"].left(110), 5.0)
	var src := "" if d.get("origin", a) == a else "  [color=#9fb0c0](source d'origine : %s, %d relais)[/color]" % [_name(d.get("origin")), int(d.get("hops", 0))]
	var said: String = d["text"] if d["text"].length() <= 150 else d["text"].left(147) + "…"
	_banner("[color=#%s][b]%s → %s[/b][/color] [color=#9fb0c0]%s[/color]  « %s »%s" % [col.to_html(false), _name(a), ", ".join(to.map(func(x): return _name(x))), KIND_FR.get(d.get("kind", ""), ""), said, src])

func _push_hist() -> void:
	hist.append([t_sim, float(ind.get("common_context", 0)), float(ind.get("convergence_refuge", 0)), float(ind.get("safe_pct", 0))])
	if hist.size() > 600:
		hist.pop_front()
	chart.queue_redraw()

func _draw_chart() -> void:
	var w := chart.size.x
	var h := chart.size.y
	chart.draw_rect(Rect2(0, 0, w, h), Color(1, 1, 1, 0.04))
	for k in 5:
		chart.draw_line(Vector2(0, h * k / 4.0), Vector2(w, h * k / 4.0), Color(1, 1, 1, 0.06))
	var t0: float = run_info.get("t_start", 540.0)
	var span: float = max(1.0, t_end - t0)
	var cols := [Color("ffd23f"), Color("53d6ff"), Color("3ddc84")]
	for s in 3:
		var pts := PackedVector2Array()
		for p in hist:
			pts.append(Vector2((p[0] - t0) / span * w, h - p[s + 1] * h))
		if pts.size() > 1:
			chart.draw_polyline(pts, cols[s], 2.0, true)
	for m in [[run_info.get("update_at", 570.0), "mairie"], [565.0, ""]]:
		pass

# ---------- animation ----------
func _animate(delta: float) -> void:
	level_shown = lerp(level_shown, level, min(1.0, delta * 2.0))
	storm = clamp((level_shown + 0.2) / 0.9, 0.0, 1.0)
	world.set_level(level_shown, storm)
	world.set_night(storm)
	bulletin_t = max(0.0, bulletin_t - delta)
	world.pulse(Time.get_ticks_msec() / 1000.0, bulletin_t > 0)
	rain_mat.albedo_color.a = 0.08 + 0.25 * storm
	rain.position = cam.global_position + Vector3(0, 18, 0) - cam.global_transform.basis.z * 20.0
	sun.light_energy = 1.35 - 0.6 * storm
	sun.light_color = Color("ffd6a8").lerp(Color("ff8a7a"), storm)
	sky_mat.sky_top_color = SKY_TOP.lerp(Color("2e2747"), storm)
	sky_mat.sky_horizon_color = SKY_HOR.lerp(Color("b4526a"), storm)
	sky_mat.ground_horizon_color = sky_mat.sky_horizon_color
	env.fog_density = (0.0002 + 0.0012 * storm) * clamp(110.0 / max(dist, 1.0), 0.12, 1.0)   # brume légère : l'image reste nette
	for id in agents:
		var v = agents[id]
		v.tick(delta, world.walk_y(v.position.x, v.position.z), id == sel)
	if sel != "" and agents.has(sel):
		ring.visible = true
		ring.position = agents[sel].position + Vector3(0, 0.05, 0)
	else:
		ring.visible = false
	for p in pulses:
		p["t"] += delta
	pulses = pulses.filter(func(p): return p["t"] < p["dur"] and is_instance_valid(p["from"]) and is_instance_valid(p["to"]))
	for wv in waves:
		wv["t"] += delta
	waves = waves.filter(func(wv): return wv["t"] < 2.5)
	_draw_fx()

var fx_mesh := ImmediateMesh.new()
var fx_inst: MeshInstance3D
func _draw_fx() -> void:
	if fx_inst == null:
		fx_inst = MeshInstance3D.new()
		fx_inst.mesh = fx_mesh
		var m := StandardMaterial3D.new()
		m.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		m.vertex_color_use_as_albedo = true
		m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		m.no_depth_test = true
		fx_inst.material_override = m
		fx_inst.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(fx_inst)
	fx_mesh.clear_surfaces()
	if pulses.is_empty() and waves.is_empty() and (sel == "" or not agents.has(sel)):
		return
	fx_mesh.surface_begin(Mesh.PRIMITIVE_LINES)
	for p in pulses:                                  # impulsion qui voyage d'un habitant à l'autre (message)
		var a: Vector3 = p["from"].position + Vector3(0, 1.9, 0)
		var b: Vector3 = p["to"].position + Vector3(0, 1.9, 0)
		var u: float = p["t"] / p["dur"]
		var lift: float = min(14.0, a.distance_to(b) * 0.3)
		var prev := a
		for i in range(1, 21):
			var s := i / 20.0
			var q := a.lerp(b, s) + Vector3(0, sin(s * PI) * lift, 0)
			var c: Color = p["col"]
			c.a = 0.18 + 0.75 * clamp(1.0 - abs(s - u) * 5.0, 0.0, 1.0)
			fx_mesh.surface_set_color(c)
			fx_mesh.surface_add_vertex(prev)
			fx_mesh.surface_add_vertex(q)
			prev = q
		var head := a.lerp(b, u) + Vector3(0, sin(u * PI) * lift, 0)
		for k in 6:                                   # petite étoile au front de l'impulsion
			var ang := k * PI / 3.0
			fx_mesh.surface_set_color(p["col"])
			fx_mesh.surface_add_vertex(head)
			fx_mesh.surface_add_vertex(head + Vector3(cos(ang), sin(ang), 0) * 0.6)
	for wv in waves:                                  # onde d'un bulletin ou d'une rumeur
		var r: float = wv["t"] * 22.0
		var c: Color = wv["col"]
		c.a = clamp(1.0 - wv["t"] / 2.5, 0.0, 1.0)
		for i in 48:
			var a0 := i * TAU / 48.0
			var a1 := (i + 1) * TAU / 48.0
			fx_mesh.surface_set_color(c)
			fx_mesh.surface_add_vertex(wv["pos"] + Vector3(cos(a0) * r, 0, sin(a0) * r))
			fx_mesh.surface_add_vertex(wv["pos"] + Vector3(cos(a1) * r, 0, sin(a1) * r))
	if sel != "" and agents.has(sel):                 # réseau de l'habitant sélectionné
		for c in ainfo[sel].get("contacts", []):
			if agents.has(c):
				fx_mesh.surface_set_color(Color(1, 0.88, 0.54, 0.35))
				fx_mesh.surface_add_vertex(agents[sel].position + Vector3(0, 0.3, 0))
				fx_mesh.surface_add_vertex(agents[c].position + Vector3(0, 0.3, 0))
	fx_mesh.surface_end()

# ---------- caméra ----------
func _unhandled_input(e: InputEvent) -> void:
	if e is InputEventMouseButton:
		if e.button_index == MOUSE_BUTTON_WHEEL_UP and e.pressed:
			dist = max(8.0, dist * 0.9)
		elif e.button_index == MOUSE_BUTTON_WHEEL_DOWN and e.pressed:
			dist = min(700.0, dist * 1.1)
		elif e.button_index == MOUSE_BUTTON_RIGHT:
			dragging = 1 if e.pressed else 0
		elif e.button_index == MOUSE_BUTTON_MIDDLE:
			dragging = 2 if e.pressed else 0
		elif e.button_index == MOUSE_BUTTON_LEFT and e.pressed:
			_pick(e.position)
	elif e is InputEventMouseMotion and dragging > 0:
		if dragging == 1:
			yaw -= e.relative.x * 0.005
			pitch = clamp(pitch + e.relative.y * 0.004, 0.15, 1.45)
		else:
			var r := cam.global_transform.basis.x
			var f := Vector3(-sin(yaw), 0, -cos(yaw))
			focus -= (r * e.relative.x - f * e.relative.y) * dist * 0.0015
	elif e is InputEventKey and e.pressed and not e.echo:
		match e.keycode:
			KEY_A: _on_button("alert")
			KEY_U: _on_button("rumor")
			KEY_B: _on_button("bridge")
			KEY_SPACE: _on_button("pause")
			KEY_M: _on_button("mode")
			KEY_N: _on_button("scen")
			KEY_R: _on_button("restart")
			KEY_G: _on_button("cells")
			KEY_C: _on_button("cine")
			KEY_F: _on_button("follow")
			KEY_I: _on_button("text")
			KEY_P:                                    # flou miniature (tilt-shift) : activer / couper
				cam_attr.dof_blur_far_enabled = not cam_attr.dof_blur_far_enabled
				cam_attr.dof_blur_near_enabled = cam_attr.dof_blur_far_enabled
			KEY_T: pitch = 1.42 if pitch < 1.3 else 0.72
			KEY_TAB:
				var ids := agents.keys()
				ids.sort()
				if not ids.is_empty():
					var i := ids.find(sel)
					sel = ids[(i + 1) % ids.size()]
			KEY_ESCAPE: sel = ""

func _pick(p: Vector2) -> void:
	for id in pop_pos:                                # clic sur une pastille de la frise des habitants
		if p.distance_to(pop_pos[id]) < 11.0:
			sel = id
			return
	var best := ""
	var bd := 40.0
	for id in agents:
		var v = agents[id]
		if cam.is_position_behind(v.position):
			continue
		var sp := cam.unproject_position(v.position + Vector3(0, 1.0, 0))
		var dd := sp.distance_to(p)
		if dd < bd:
			bd = dd
			best = id
	sel = best

func _camera(delta: float) -> void:
	var mv := Vector3.ZERO
	var f := Vector3(-sin(yaw), 0, -cos(yaw))
	var r := Vector3(cos(yaw), 0, -sin(yaw))
	if Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_Z) or Input.is_key_pressed(KEY_UP):
		mv += f
	if Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN):
		mv -= f
	if Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT):
		mv += r
	if Input.is_key_pressed(KEY_Q) or Input.is_key_pressed(KEY_LEFT):
		mv -= r
	focus += mv * dist * 0.6 * delta
	if cine:
		yaw += delta * 0.08
	if follow and sel != "" and agents.has(sel):
		focus = focus.lerp(agents[sel].position, min(1.0, delta * 3.0))
		dist = lerp(dist, 30.0, min(1.0, delta))
	var off := Vector3(sin(yaw) * cos(pitch), sin(pitch), cos(yaw) * cos(pitch)) * dist
	cam.position = cam.position.lerp(focus + off, min(1.0, delta * 6.0)) if cam.position != Vector3.ZERO else focus + off
	cam.look_at(focus + Vector3(0, 1, 0), Vector3.UP)
	var d := cam.position.distance_to(focus)
	cam_attr.dof_blur_far_distance = d * 1.15
	cam_attr.dof_blur_far_transition = d * 0.8
	cam_attr.dof_blur_near_distance = d * 0.55
	cam_attr.dof_blur_near_transition = d * 0.35

# ---------- panneaux ----------
func _pct(x) -> String:
	return "—" if x == null else "%d %%" % int(round(float(x) * 100))

func _bar(x: float, w := 16) -> String:
	var n := int(round(clamp(x, 0.0, 1.0) * w))
	return "█".repeat(n) + "░".repeat(w - n)

func _hud() -> void:
	var vs := get_viewport().get_visible_rect().size
	left_panel.size.y = vs.y - 28
	right_panel.position.x = vs.x - right_panel.size.x - 12
	title_panel.position.x = (324 + right_panel.position.x - title_panel.size.x) * 0.5
	feed_panel.position.y = vs.y - feed_panel.size.y - 14
	feed_panel.visible = not feed.is_empty()
	vis.queue_redraw()
	var cnt := {}
	for v in agents.values():
		cnt[v.state] = cnt.get(v.state, 0) + 1
	title_lbl.text = "[b]CRUE À RIVE-CLAIRE[/b]  [color=#8899aa]%s · scénario %s[/color]\n[color=#3ddc84]%d à l'abri[/color] · [color=#f4f1e8]%d en route[/color] · [color=#f2b134]%d se préparent[/color] · [color=#e5484d]%d bloqué(s)[/color] · [color=#9b8cff]%d à l'étage chez soi[/color] · [color=#8fa3b8]%d chez soi[/color]" % [
		_hm(t_sim), run_info.get("scenario", "?"), cnt.get("arrived", 0), cnt.get("walking", 0), cnt.get("preparing", 0), cnt.get("blocked", 0), cnt.get("sheltered", 0), cnt.get("home", 0)]
	for id in cards:                                  # fiches des habitants, mises à jour en continu
		var v = agents.get(id)
		if v == null:
			continue
		var c: Array = cards[id]
		var i: Dictionary = ainfo[id]
		var b: Dictionary = beliefs.get(id, {})
		var th: Dictionary = last_think.get(id, {})
		var eng := "règles" if i["kind"] == "rule" else ("LLM grand" if i["kind"] == "big" else "LLM compact")
		var st: Color = AgentView.STATE_COL.get(v.state, Color.WHITE)
		var t := "[b]%s · %s[/b]  [color=#8899aa]%s%s[/color]\n" % [id, i.get("name", ""), eng, "  · réfléchit…" if v.thinking else ""]
		t += "[color=#%s]%s[/color]  risque %s\n" % [st.to_html(false), AgentView.STATE_FR.get(v.state, v.state), _bar(float(b.get("risk", 0)) / 100.0, 10)]
		t += "[color=#aab8c6]%s[/color]" % (("« %s »" % str(th.get("why", "")).left(48)) if not th.is_empty() else str(i.get("situation", "")).left(52))
		c[1].text = t
		c[2].bg_color = Color(1, 0.88, 0.54, 0.16) if id == sel else Color(1, 1, 1, 0.05)
	var n: int = run_info.get("n_agents", 12)
	clock_lbl.text = "ALERTE · Rive-Claire   %s" % _hm(t_sim)
	var mode: String = run_info.get("mode", "?")
	status_lbl.text = "%s · scénario %s · mode %s%s%s\nniveau d'eau %+.2f m · %s" % [
		"connecté" if ws.get_ready_state() == WebSocketPeer.STATE_OPEN else "connexion à la passerelle…",
		run_info.get("scenario", "?"), mode, "  · PAUSE" if paused else "", "  · REJEU" if replay else "", level, "vérité : pont %s" % str(truth.get("bridge", "?")).to_upper()]
	btns["pause"].text = "Reprendre (Espace)" if paused else "Pause (Espace)"
	btns["follow"].modulate = Color("ffe08a") if follow else Color.WHITE
	btns["cine"].modulate = Color("ffe08a") if cine else Color.WHITE
	if not ind.is_empty():
		var msgs: Dictionary = ind.get("messages", {})
		var load: Dictionary = ind.get("refuge_load", {})
		var s := "[b]Indicateurs[/b]  [color=#8899aa](calculés en flux par la passerelle, %s)[/color]\n" % _hm(t_sim)
		s += "[color=#3ddc84]À l'abri[/color]  %d / %d   %s\n" % [ind.get("safe", 0), n, _bar(float(ind.get("safe_pct", 0)))]
		s += "Départs  %d · délai moyen %s\n" % [ind.get("departed", 0), ("+%.1f min" % ind["depart_delay_avg"]) if ind.get("depart_delay_avg") != null else "—"]
		s += "[color=#ffd23f]Contexte commun[/color]  %s  [color=#8899aa](moyen %s)[/color]\n" % [_pct(ind.get("common_context")), _pct(ind.get("common_context_avg"))]
		s += "  exactitude : pont %s · refuge %s · danger %s\n" % [_pct(ind.get("exact_bridge")), _pct(ind.get("exact_refuge")), _pct(ind.get("exact_danger"))]
		s += "[color=#53d6ff]Convergence[/color]  refuge %s (%s) · pont %s (%s)\n" % [_pct(ind.get("convergence_refuge")), str(ind.get("refuge_mode", "—")), _pct(ind.get("convergence_bridge")), str(ind.get("bridge_mode", "—"))]
		s += "Messages  %d transmis · %d relayés · %d vérifiés · %d corrigés · %d rumeurs\n" % [msgs.get("message", 0) + msgs.get("observation", 0), msgs.get("relais", 0), ind.get("verified", 0), msgs.get("correction", 0), msgs.get("rumeur", 0)]
		s += "  relais de la rumeur avant correction : %d%s\n" % [ind.get("rumor_relays_before_correction", 0), " (corrigée)" if ind.get("rumor_corrected", false) else ""]
		for r in load:
			var l: Array = load[r]
			s += "%s  %d/%d  %s\n" % [refuge_names.get(r, r), l[0], l[1], _bar(float(l[0]) / max(1, l[1]), 12)]
			world.set_refuge(r, int(l[0]), int(l[1]), refuge_names.get(r, r))
		s += "Mobilité  %d bloqué(s) · %.1f min dans l'eau en moyenne\n" % [ind.get("blocked", 0), float(ind.get("wet_avg_min", 0))]
		s += "[color=#8899aa]source : événement n°%s (%s), décalage %s ms[/color]\n" % [str(ind.get("source_seq", "")), str(ind.get("source_type", "")), str(ind.get("lag_ms", ""))]
		if not metrics.is_empty():
			s += "[b]Coût[/b]  %d appels LLM · %d+%d tokens · %d incidents · latence moy. %d ms" % [metrics.get("calls_total", 0), metrics.get("tokens_in", 0), metrics.get("tokens_out", 0), metrics.get("incidents", 0), metrics.get("avg_latency_ms", 0)]
		ind_lbl.text = s
	insp_panel.visible = sel != "" and ainfo.has(sel)
	if sel != "" and ainfo.has(sel):
		var i: Dictionary = ainfo[sel]
		var b: Dictionary = beliefs.get(sel, {})
		var th: Dictionary = last_think.get(sel, {})
		var v = agents[sel]
		var eng := "règles explicites" if i["kind"] == "rule" else ("LLM grand · %s" % i.get("model", "")) if i["kind"] == "big" else ("LLM compact · %s" % i.get("model", ""))
		var ok := func(k: String, val) -> String:
			return "[color=#3ddc84]✓[/color]" if (k == "refuge" and (val in truth.get("refuges", []) or v.state == "arrived")) or (k != "refuge" and val == truth.get(k)) else "[color=#ff6b6b]✗[/color]"
		var s := "[b]%s · %s[/b]  [color=#8899aa]%s[/color]\n%s\n" % [sel, i.get("name", ""), eng, i.get("situation", "")]
		s += "Confiance officiel %d/5 · voisins %d/5 · mobilité %.2f · seuil %d\nContacts : %s\n" % [i["trust_off"], i["trust_nb"], i["mob"], i.get("threshold", 0), ", ".join(i.get("contacts", []))]
		s += "État : [b]%s[/b] · risque perçu %d/100 %s\n" % [AgentView.STATE_FR.get(v.state, v.state), b.get("risk", 0), _bar(float(b.get("risk", 0)) / 100.0, 10)]
		if not b.is_empty():
			s += "Croit : pont %s %s · refuge %s %s · danger %s %s\n" % [b["bridge"], ok.call("bridge", b["bridge"]), b["refuge"], ok.call("refuge", b["refuge"]), "oui" if b["danger"] else "non", ok.call("danger", b["danger"])]
		if not th.is_empty():
			s += "\n[b]Dernière décision[/b] (%s%s) : %s\n[i]« %s »[/i]\n" % [th.get("decider", ""), (", %d ms" % th["latency_ms"]) if th.get("latency_ms") != null else "", ACT_FR.get(th["action"], th["action"]), th["why"]]
			var rr: Array = th.get("retrieved", [])
			if not rr.is_empty():
				s += "[color=#8899aa]Retrouvé (RAG) :[/color]\n"
				for x in rr.slice(0, 4):
					s += "[color=#aab8c6]· %s[/color]\n" % str(x).left(150)
		s += "\n[b]Mémoire / journal[/b]\n"
		var j: Array = journal.get(sel, [])
		for x in j.slice(max(0, j.size() - 9)):
			s += x + "\n"
		insp.text = s

# ---------- repères visuels sans texte ----------
var pop_pos := {}
func _draw_vis() -> void:
	var vs := vis.size
	var now := Time.get_ticks_msec() / 1000.0
	var pulse := 0.5 + 0.5 * sin(now * 4.0)
	# horloge simulée : barre fine en haut de l'écran, du bleu (calme) au rouge (crue forte)
	var t0: float = run_info.get("t_start", 540.0)
	var u: float = clamp((t_sim - t0) / max(1.0, t_end - t0), 0.0, 1.0)
	vis.draw_rect(Rect2(0, 0, vs.x, 7), Color(0, 0, 0, 0.35))
	vis.draw_rect(Rect2(0, 0, vs.x * u, 7), Color("4da3ff").lerp(Color("ff4d5e"), storm))
	for m in [run_info.get("update_at", -1.0), run_info.get("bridge_close_at", -1.0)]:   # repères : mise à jour de la mairie, fermeture du pont
		if m != null and float(m) > t0:
			var mx: float = (float(m) - t0) / max(1.0, t_end - t0) * vs.x
			vis.draw_rect(Rect2(mx - 1, 0, 2, 12), Color(1, 1, 1, 0.7))
	# liaison avec la simulation : point vert (en direct), orange (connecté, en attente), gris (déconnecté)
	var live := ws.get_ready_state() == WebSocketPeer.STATE_OPEN
	var cc := Color("3ddc84") if live and started else (Color("f2b134") if live else Color("8a8f98"))
	var rx: float = right_panel.position.x - 14 if show_text else vs.x    # bord droit de la scène (avant la colonne de droite)
	vis.draw_circle(Vector2(rx - 22, 26), 7.0 + (pulse * 2.0 if not started else 0.0), cc)
	if replay:                                        # rejeu : anneau autour du point
		vis.draw_arc(Vector2(rx - 22, 26), 12, 0, TAU, 32, Color(1, 1, 1, 0.8), 2.0)
	if paused:                                        # pause : deux barres
		vis.draw_rect(Rect2(vs.x * 0.5 - 14, 22, 9, 30), Color(1, 1, 1, 0.85))
		vis.draw_rect(Rect2(vs.x * 0.5 + 5, 22, 9, 30), Color(1, 1, 1, 0.85))
	if not started:                                   # en attente de la simulation : onde au centre
		var r := fmod(now, 2.0) * 60.0
		vis.draw_arc(vs * 0.5, r, 0, TAU, 64, Color(1, 1, 1, 0.6 * (1.0 - r / 120.0)), 3.0)
		vis.draw_circle(vs * 0.5, 8, Color(1, 1, 1, 0.8))
		return
	# niveau de l'eau : jauge verticale à gauche
	var gx: float = rx - 30.0 if show_text else 22.0
	var gy := vs.y * 0.3
	var gh := vs.y * 0.4
	var f: float = clamp((level_shown + 1.35) / 2.3, 0.0, 1.0)
	vis.draw_rect(Rect2(gx, gy, 12, gh), Color(0, 0, 0, 0.35))
	vis.draw_rect(Rect2(gx, gy + gh * (1.0 - f), 12, gh * f), Color("8a6f45").lerp(Color("d7263d"), storm))
	vis.draw_rect(Rect2(gx - 3, gy + gh * (1.0 - 1.35 / 2.3), 18, 2), Color(1, 1, 1, 0.6))     # hauteur des quais
	# habitants : une pastille par personne, couleur du gilet (état), anneau violet/cyan pour les LLM, qui pulse quand il réfléchit
	pop_pos.clear()
	if show_text:                                     # les fiches de la colonne de gauche remplacent la frise de pastilles
		return
	var ids := agents.keys()
	ids.sort()
	var x0 := 28.0
	var y0 := vs.y - 30.0
	for i in ids.size():
		var id: String = ids[i]
		var v = agents[id]
		var c := Vector2(x0 + (i % 40) * 26.0, y0 - int(i / 40) * 26.0)
		pop_pos[id] = c
		vis.draw_circle(c, 9.0, AgentView.STATE_COL.get(v.state, Color.WHITE))
		vis.draw_rect(Rect2(c.x - 6, c.y + 14, 12, 4), v.id_col)       # couleur d'identifiant = casquette de l'habitant
		var k: String = ainfo[id].get("kind", "rule")
		if k != "rule":
			var oc := Color("b48cff") if k == "big" else Color("53d6ff")
			oc.a = (0.4 + 0.6 * pulse) if v.thinking else 0.8
			vis.draw_arc(c, 12.0 + (pulse * 2.0 if v.thinking else 0.0), 0, TAU, 24, oc, 2.0)
		if id == sel:
			vis.draw_arc(c, 16.0, 0, TAU, 24, Color("ffe08a"), 2.5)
	# habitant sélectionné : risque perçu (barre) et justesse de ses croyances (pont, refuge, danger : vert = juste, rouge = faux)
	if sel != "" and agents.has(sel) and pop_pos.has(sel):
		var b: Dictionary = beliefs.get(sel, {})
		var base: Vector2 = pop_pos[sel] + Vector2(-10, -30)
		var risk: float = clamp(float(b.get("risk", 0)) / 100.0, 0.0, 1.0)
		vis.draw_rect(Rect2(base.x, base.y - 60, 8, 60), Color(0, 0, 0, 0.4))
		vis.draw_rect(Rect2(base.x, base.y - 60 * risk, 8, 60 * risk), Color("3ddc84").lerp(Color("ff4d5e"), risk))
		if not b.is_empty():
			var v = agents[sel]
			var oks := [b["bridge"] == truth.get("bridge"), b["refuge"] in truth.get("refuges", []) or v.state == "arrived", b["danger"] == truth.get("danger")]
			for j in 3:
				vis.draw_circle(base + Vector2(22, -50 + j * 20), 6.0, Color("3ddc84") if oks[j] else Color("ff4d5e"))
