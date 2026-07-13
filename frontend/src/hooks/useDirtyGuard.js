import { useEffect, useRef, useState } from 'react';

/**
 * iter90et : Detecte si `value` a change depuis l'ouverture de `open`.
 *
 * Usage :
 *   const dirty = useDirtyGuard(form, dialogOpen);
 *   <Dialog hasUnsavedChanges={dirty} open={dialogOpen} onOpenChange={setDialogOpen}>
 *
 * A l'ouverture, snapshot JSON de `value` dans un ref.
 * A chaque changement de `value`, compare JSON pour retourner dirty=true si diff.
 * A la fermeture, reset. Zero overhead si dialog ferme.
 */
export function useDirtyGuard(value, open) {
  const initialRef = useRef(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (open) {
      // Snapshot au moment de l'ouverture
      try {
        initialRef.current = JSON.stringify(value);
      } catch {
        initialRef.current = null;
      }
      setDirty(false);
    } else {
      initialRef.current = null;
      setDirty(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open || initialRef.current === null) return;
    try {
      setDirty(JSON.stringify(value) !== initialRef.current);
    } catch {
      // valeur non serialisable : on considere dirty par prudence
      setDirty(true);
    }
  }, [value, open]);

  return dirty;
}
