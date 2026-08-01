// Existem quatro identificadores no sistema e trocar um pelo outro compila silenciosamente.
//
// O par que realmente importa é TrackId × TrackUri: `4iV5W9uYEdYUVa79Axb7Rh` e
// `spotify:track:4iV5W9uYEdYUVa79Axb7Rh` são os dois `string`, e mandar o TrackId onde o
// Spotify quer TrackUri é aceito pelo compilador, aceito pelo fetch, e FALHA NO SERVIDOR.
//
// O único `as` do código-fonte inteiro está na fronteira onde o dado entra (api.ts); depois
// disso o compilador cuida (RNF-22).

declare const brand: unique symbol
type Brand<T, B> = T & { readonly [brand]: B }

export type TrackId = Brand<string, 'TrackId'>
export type TrackUri = Brand<string, 'TrackUri'>
export type PlayId = Brand<number, 'PlayId'>
export type GuestId = Brand<number, 'GuestId'>
