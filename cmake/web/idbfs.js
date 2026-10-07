// Linked with --pre-js, so FS and IDBFS are the module's own. SDL keeps preferences under /libsdl:
// that folder is IndexedDB-backed, loaded before main runs, and flushed by Module.yySync after each write.
Module['preRun'] = Module['preRun'] || [];
Module['preRun'].push(function () {
  FS.mkdir('/libsdl');
  var running = false, again = false;
  Module['yySync'] = function () {
    if (running) { again = true; return; }
    running = true;
    FS.syncfs(false, function (err) {
      if (err) console.warn('Saving preferences failed:', err);
      running = false;
      if (again) { again = false; Module['yySync'](); }
    });
  };
  try { FS.mount(IDBFS, {}, '/libsdl'); } catch (e) { console.warn('Preferences will not persist:', e); Module['yySync'] = null; return; }
  addRunDependency('yy-idbfs');
  FS.syncfs(true, function (err) {
    if (err) console.warn('Loading preferences failed:', err);
    removeRunDependency('yy-idbfs');
  });
});
