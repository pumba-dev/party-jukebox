import { createRouter, createWebHistory } from 'vue-router'

// Uma SPA, três rotas — não três builds. As telas compartilham a store, os tipos e o cliente
// (.docs/08-frontend.md §1). O servidor devolve index.html para qualquer rota não-/api.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'guest', component: () => import('./views/GuestView.vue') },
    { path: '/tv', name: 'tv', component: () => import('./views/TvView.vue') },
    { path: '/host', name: 'host', component: () => import('./views/HostView.vue') },
    { path: '/:rest(.*)', redirect: '/' },
  ],
})
