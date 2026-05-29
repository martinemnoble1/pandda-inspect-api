import { configureStore } from "@reduxjs/toolkit";
// Moorhen 0.23 exposes a single combined reducer map. If this import fails with
// a missing 'generalStates', clear node_modules/.vite and restart vite.
import { MoorhenStoreReducers } from "moorhen";

if (!MoorhenStoreReducers || !("generalStates" in MoorhenStoreReducers)) {
  throw new Error(
    "moorhen MoorhenStoreReducers missing 'generalStates' — clear the Vite " +
      "dep cache (node_modules/.vite) and restart."
  );
}

export const store = configureStore({
  reducer: { ...MoorhenStoreReducers },
  middleware: (getDefault) => getDefault({ serializableCheck: false }),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
export default store;
