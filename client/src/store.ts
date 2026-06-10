import {
  configureStore,
  combineReducers,
  type Action,
} from "@reduxjs/toolkit";
// Moorhen 0.23 exposes a single combined reducer map. If this import fails with
// a missing 'generalStates', clear node_modules/.vite and restart vite.
import { MoorhenStoreReducers } from "moorhen";

if (!MoorhenStoreReducers || !("generalStates" in MoorhenStoreReducers)) {
  throw new Error(
    "moorhen MoorhenStoreReducers missing 'generalStates' — clear the Vite " +
      "dep cache (node_modules/.vite) and restart."
  );
}

// Reset the ENTIRE Moorhen store to its boot/initial state in one dispatch.
//
// WHY this exists: this store is an app-level module SINGLETON, shared by the
// app `<Provider>` AND handed to MoorhenContainer. But Moorhen's Coot instance
// is per-mount. On unmount, MoorhenInstanceProvider's cleanup terminates the
// CootWorker — yet it leaves THIS store fully populated. The slices' initial
// values run only at app boot (the store is never re-created), and nothing
// dispatches them back, so a cascade of init/ready flags (cootInitialized,
// globalUI.isGlobalInstanceReady, generalStates.userPreferencesMounted) and the
// maps/molecules slices go stale. On the NEXT mount the menu/managers render
// against those stale flags before the fresh instance has run startInstance →
// crashes (e.g. `null.mainMenuMap`, dead map/molecule objects). Resetting EVERY
// slice to initial (passing undefined state makes each slice reducer yield its
// initialState) makes the next entry replay a clean boot, instead of patching
// one stale flag at a time. Dispatched by InspectPage's unmount teardown.
export const RESET_MOORHEN_STORE = "pandda/resetMoorhenStore";
export const resetMoorhenStore = () => ({ type: RESET_MOORHEN_STORE });

const combined = combineReducers({ ...MoorhenStoreReducers });
const rootReducer = (
  state: ReturnType<typeof combined> | undefined,
  action: Action
) =>
  action.type === RESET_MOORHEN_STORE
    ? combined(undefined, action)
    : combined(state, action);

export const store = configureStore({
  reducer: rootReducer,
  middleware: (getDefault) => getDefault({ serializableCheck: false }),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
export default store;
