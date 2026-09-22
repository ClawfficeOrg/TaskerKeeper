import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  // Needed for <script lang="ts"> in .svelte files.
  preprocess: vitePreprocess(),
  kit: {
    // node adapter: `node build` serves on PORT (compose maps 3000).
    adapter: adapter({ out: 'build' })
  }
};

export default config;
