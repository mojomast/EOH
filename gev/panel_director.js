// Director bridge. God's Eye View exposes SceneDirector on window.__godsEyeView
// (listScenes, startScene, stopScene, importProjectFile). Voice control_scene
// only lists, plays, stops, and advances. Creating a scene is an import of the
// current project with one scene replaced or appended. A missing director is an
// error, not a guessed endpoint.
(function () {
  if (window.__hermesDirector) return

  const fail = (error, message, fix, extra) => ({
    ok: false, error, code: error, message, fix, ...(extra || {}),
  })

  const director = () => {
    const found = window.__godsEyeView && window.__godsEyeView.sceneDirector
    if (!found || typeof found.listScenes !== 'function'
      || typeof found.startScene !== 'function' || typeof found.stopScene !== 'function') return null
    return found
  }

  const missing = () => fail(
    'director_unavailable',
    'This pane has no Director. window.__godsEyeView.sceneDirector is missing.',
    'Open a God\'s Eye View v0.3 pane and retry. Built-in playback is godseye_scene op=playback.',
  )

  const listed = (d) => {
    let scenes = []
    try { scenes = d.listScenes() || [] } catch { scenes = [] }
    let status = {}
    try { status = typeof d.getPlaybackStatus === 'function' ? (d.getPlaybackStatus() || {}) : {} } catch { status = {} }
    return { scenes, status }
  }

  async function apply(d, args) {
    if (typeof d.importProjectFile !== 'function') {
      return fail(
        'director_import_unavailable',
        'Director can list and play scenes, but this build has no importProjectFile.',
        'Use godseye_scene op=playback to play a built-in scene. Creating one needs SceneDirector.importProjectFile.',
        { scenes: listed(d).scenes },
      )
    }
    const current = d._project
    if (!current || !Array.isArray(current.scenes)) {
      return fail(
        'director_project_unreadable',
        'Director is present, but its saved project cannot be read.',
        'Use godseye_scene op=playback action=list for scenes the pane already has.',
      )
    }
    const scene = args.scene
    if (!scene || typeof scene !== 'object' || !scene.id) {
      return fail('bad_scene', 'The scene document has no id.', 'Pass a scene with an id and at least one shot.')
    }
    let scenes
    try { scenes = JSON.parse(JSON.stringify(current.scenes)) }
    catch { return fail('director_project_unreadable', 'Director\'s project could not be copied.', 'Retry. If it still fails, the pane project is not plain JSON.') }
    const index = scenes.findIndex((item) => item && item.id === scene.id)
    const mode = String(args.mode || 'compose')
    if (mode === 'update' && index < 0) {
      return fail(
        'scene_not_found',
        `No Director scene ${scene.id}.`,
        'Compose the scene first, or pass an id from godseye_scene op=director_list.',
        { scenes: listed(d).scenes },
      )
    }
    if (index >= 0) scenes[index] = scene
    else scenes.push(scene)
    const project = {
      version: current.version || 6,
      ...(current.createdAt ? { createdAt: current.createdAt } : {}),
      updatedAt: new Date().toISOString(),
      ...(Array.isArray(current.installedBuiltInSceneIds)
        ? { installedBuiltInSceneIds: current.installedBuiltInSceneIds }
        : {}),
      scenes,
    }
    if (args.version != null) project.version = args.version
    let applied = false
    try {
      applied = await d.importProjectFile({ name: 'hermes-director.json' }, {
        prepared: { project, assets: [] },
        selection: { sceneId: scene.id, shotId: (scene.shots && scene.shots[0] && scene.shots[0].id) || null },
      })
    } catch (error) {
      return fail(
        'director_import_failed',
        `Director rejected the scene: ${error && error.message ? error.message : error}`,
        'Camera heights are meters above the ellipsoid. Ids must be unique. Third-party video URLs are not scene media.',
      )
    }
    if (applied !== true) {
      return fail(
        'director_import_rejected',
        'Director did not apply the scene.',
        'Open Director in the pane and confirm the project can be imported, then retry.',
      )
    }
    const after = listed(d)
    const hit = (after.scenes || []).find((item) => item && item.id === scene.id)
    return {
      ok: true,
      applied: true,
      scene: hit || { id: scene.id, title: scene.title || scene.id, shots: (scene.shots || []).length },
      scenes: after.scenes,
    }
  }

  function play(d, args) {
    if (d.running) {
      return fail(
        'scene_running',
        'A Director scene is already running.',
        'Call godseye_scene op=director_stop, then play again.',
      )
    }
    const query = args.id || args.sceneId || args.scene_id
    if (!query || typeof d.findSceneByQuery !== 'function') {
      return fail('bad_args', 'Play needs a scene id or title.', 'Pass id from godseye_scene op=director_list.')
    }
    const scene = d.findSceneByQuery(query)
    if (!scene) {
      return fail(
        'scene_not_found',
        `No Director scene matched ${query}.`,
        'Compose the scene, or pick an id from godseye_scene op=director_list.',
        { scenes: listed(d).scenes },
      )
    }
    try { d.startScene(scene.id, { single: true }) }
    catch (error) {
      return fail('director_play_failed', `Director did not start: ${error && error.message ? error.message : error}`,
        'Stop playback and retry. If Director is missing, the error is director_unavailable.')
    }
    return { ok: true, playing: scene.title || scene.id, id: scene.id, shots: scene.shots }
  }

  async function command(args) {
    const d = director()
    if (!d) return missing()
    const action = String((args && args.action) || 'list')
    if (action === 'list' || action === 'status') {
      const snap = listed(d)
      return { ok: true, scenes: snap.scenes, ...snap.status }
    }
    if (action === 'stop') {
      try { d.stopScene('Stopped by Hermes') }
      catch (error) {
        return fail('director_stop_failed', `Director did not stop: ${error && error.message ? error.message : error}`,
          'Retry godseye_scene op=director_stop.')
      }
      return { ok: true, running: false }
    }
    if (action === 'play') return play(d, args || {})
    if (action === 'apply') return apply(d, args || {})
    return fail('bad_args', `Unknown Director action ${action}.`, 'Use list, play, stop, or apply.')
  }

  window.__hermesDirector = { command }
})()
