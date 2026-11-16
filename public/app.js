/*
 * DATA FLOW
 *
 * The page reads JSON files from a base folder. The default is "data/".
 * A "?data=some/folder/" query parameter changes the base folder (used for tests).
 *
 * 1. Page load:
 *      <base>index.json           state names, ban counts, source names and urls
 *      <base>books_unbanned.json  the Books Unbanned programs
 * 2. A person opens #state=TX (by a map click, the state list, or a link):
 *      <base>states/TX.json       books, districts, and ban events for that state
 * 3. A person opens #zip=75001 (by the ZIP form or a link):
 *      <base>zips/750.json        districts and libraries for every ZIP that starts with 750
 *      <base>states/TX.json       one state file for each state listed for that ZIP
 *
 * Each file loads once. The page keeps the parsed result in memory (see fetchJson).
 * The page never loads a file it does not show.
 *
 * Terms used below:
 *   ban event  one book banned in one place (a district or a whole state) in one school year.
 *   group      one book plus its ban events inside one section. A group is one card.
 *   section    a titled grid of cards, for example "Statewide bans in Texas".
 *   view       everything the page shows for one route: the place, its sections, its libraries.
 *   route      the part of the URL after "#": "zip=75001", "state=TX", or nothing (the landing page).
 *
 * SECURITY RULE: build every element with el() or createElement and textContent.
 * Never put file data into innerHTML. Every link from data goes through safeUrl().
 *
 * docs/UI.md explains each part of this file in plain language.
 */
"use strict";

const PAGE_SIZE = 24; // cards drawn at first in a section, and added by each "Show more" click
const RANK_PAGE = 10; // districts drawn at first in the top districts list
const SEARCH_DELAY_MS = 150; // wait after the last key press before the search filter runs
const EVERYLIBRARY_URL = "https://www.everylibrary.org/billtracking";

const STATUS_ORDER = ["banned", "banned_pending_investigation", "restricted"]; // most serious first
const STATUS_LABELS = {
	banned: "Banned",
	banned_pending_investigation: "Banned pending investigation",
	restricted: "Restricted",
};
const NO_YEAR = "__none"; // the year filter value that means "the source gave no school year"

// Map shading: one step per range of ban events. The colors live in app.css as --map-1 to --map-5.
// TODO: replace these ranges with quantiles if the counts change a lot after a data refresh.
const MAP_STEPS = [
	{ min: 1, max: 9, label: "1 to 9", cssVar: "--map-1" },
	{ min: 10, max: 49, label: "10 to 49", cssVar: "--map-2" },
	{ min: 50, max: 199, label: "50 to 199", cssVar: "--map-3" },
	{ min: 200, max: 999, label: "200 to 999", cssVar: "--map-4" },
	{ min: 1000, max: Infinity, label: "1,000 or more", cssVar: "--map-5" },
];

const DATA_BASE = readDataBase();
const fileCache = new Map(); // file name -> promise of parsed JSON
const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });

let siteIndex = null; // parsed index.json, or null if it failed to load
let indexFailed = false;
let routeToken = 0; // grows with each route change, so an old slow fetch cannot overwrite a newer view
let view = null; // the view on screen, or null on the landing page
let filters = defaultFilters();
let filterUi = null; // references to the filter controls of the current view
let siteIndexSettled = false; // true once index.json has loaded or failed
let spy = null; // the IntersectionObserver that marks the current section in the section bar

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

// Read the base folder from ?data=. Accept only a plain relative path.
function readDataBase() {
	const raw = new URLSearchParams(location.search).get("data");
	const isPlain = raw && /^[A-Za-z0-9_\-./]+$/.test(raw) && !raw.startsWith("/") && !raw.includes("..");
	const base = isPlain ? raw : "data/";
	return base.endsWith("/") ? base : base + "/";
}

// Make an element. The text goes in with textContent, so data never turns into HTML.
function el(tag, className, text) {
	const node = document.createElement(tag);
	if (className) node.className = className;
	if (text !== undefined && text !== null) node.textContent = text;
	return node;
}

// Return an absolute http(s) URL string, or null for anything else (such as javascript:).
function safeUrl(value) {
	if (typeof value !== "string" || !value) return null;
	try {
		const parsed = new URL(value, location.href);
		return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
	} catch (e) {
		return null;
	}
}

// Make a link that opens in a new tab. Return plain text when the URL is not safe.
function makeLink(url, text, className) {
	const href = safeUrl(url);
	if (!href) return el("span", null, text);
	const a = el("a", className, text);
	a.href = href;
	a.target = "_blank";
	a.rel = "noopener";
	return a;
}

// Load one JSON file from the base folder. Cache it. A failed load is not cached.
function fetchJson(file) {
	if (!fileCache.has(file)) {
		const promise = fetch(DATA_BASE + file).then((response) => {
			if (!response.ok) {
				const error = new Error("HTTP " + response.status + " for " + file);
				error.status = response.status;
				throw error;
			}
			return response.json();
		});
		promise.catch(() => fileCache.delete(file));
		fileCache.set(file, promise);
	}
	return fileCache.get(file);
}

function formatNumber(n) {
	return Number(n).toLocaleString("en-US");
}

function plural(n, one, many) {
	return n === 1 ? one : many || one + "s";
}

// "2026-11-15" -> "November 15, 2026". Return the input when it is not a date.
function formatDate(text) {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || "");
	if (!m) return text || "";
	return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
}

// Lowercase and drop accents, so "andre" finds "André".
function fold(text) {
	return String(text || "")
		.toLowerCase()
		.normalize("NFD")
		.replace(/[̀-ͯ]/g, "");
}

function byId(id) {
	return document.getElementById(id);
}

function showMessage(text) {
	const box = byId("message");
	box.textContent = text;
	box.hidden = false;
}

function clearMessage() {
	const box = byId("message");
	box.textContent = "";
	box.hidden = true;
}

function cssVar(name) {
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---------------------------------------------------------------------------
// Index: state names and counts
// ---------------------------------------------------------------------------

function indexState(code) {
	return siteIndex && siteIndex.states ? siteIndex.states[code] : undefined;
}

function banCount(code) {
	const info = indexState(code);
	return info ? info.bans || 0 : 0;
}

// The map data knows every state name, even when index.json failed to load.
function knownStateName(code) {
	const spec = window.simplemaps_usmap_mapdata && simplemaps_usmap_mapdata.state_specific[code];
	const info = indexState(code);
	return (info && info.name) || (spec && spec.name) || null;
}

function stateName(code, stateData) {
	return (stateData && stateData.name) || knownStateName(code) || code;
}

// All states that the map shows, as [code, name], sorted by name.
function mapStates() {
	const specific = window.simplemaps_usmap_mapdata ? simplemaps_usmap_mapdata.state_specific : {};
	return Object.keys(specific)
		.filter((code) => specific[code].hide !== "yes")
		.map((code) => [code, knownStateName(code) || code])
		.sort((a, b) => collator.compare(a[1], b[1]));
}

// Load index.json once at page start. A failure sets a flag, and the page shows a warning.
const indexPromise = fetchJson("index.json")
	.then((data) => {
		siteIndex = data;
		return data;
	})
	.catch(() => {
		indexFailed = true;
		showMessage("Some site information could not be loaded. Counts on the map are missing and sources show as short IDs.");
		return null;
	});

// Fill the landing page pieces that depend on index.json.
function renderIndexParts() {
	// National summary.
	const states = siteIndex && siteIndex.states ? Object.values(siteIndex.states) : [];
	if (states.length) {
		const totals = siteIndex.totals || {};
		const events = totals.bans !== undefined ? totals.bans : states.reduce((sum, s) => sum + (s.bans || 0), 0);
		let books = totals.books;
		let booksLabel = "books banned";
		if (books === undefined) {
			// TODO: use index.json "totals.books" when the pipeline writes it. A book banned in two states counts twice here.
			books = states.reduce((sum, s) => sum + (s.books || 0), 0);
			booksLabel = "books, counted once per state";
		}
		byId("stat-events").textContent = formatNumber(events);
		byId("stat-books").textContent = formatNumber(books);
		byId("stat-books-label").textContent = booksLabel;
		byId("stat-states").textContent = formatNumber(states.filter((s) => s.bans > 0).length);
		byId("national-stats").hidden = false;
	}

	// About the data.
	const list = byId("about-sources");
	list.replaceChildren();
	const sources = siteIndex && siteIndex.sources ? Object.entries(siteIndex.sources) : [];
	sources.forEach(([id, info]) => {
		const item = el("li");
		item.appendChild(info.url ? makeLink(info.url, info.name || id) : el("span", null, info.name || id));
		list.appendChild(item);
	});
	if (!sources.length) list.appendChild(el("li", null, "The list of sources could not be loaded."));
	byId("about-generated").textContent = siteIndex && siteIndex.generated ? "Data generated on " + formatDate(siteIndex.generated) + "." : "";
}

// ---------------------------------------------------------------------------
// State list (the keyboard path to a state)
// ---------------------------------------------------------------------------

function fillStateSelect(select) {
	select.replaceChildren();
	const first = el("option", null, "Pick a state");
	first.value = "";
	select.appendChild(first);
	mapStates().forEach(([code, name]) => {
		const n = banCount(code);
		const label = siteIndex ? name + (n ? " (" + formatNumber(n) + ")" : " (none)") : name;
		const option = el("option", null, label);
		option.value = code;
		select.appendChild(option);
	});
}

function wireStateSelect(select) {
	select.addEventListener("change", () => {
		if (select.value) navigate("state=" + select.value);
	});
}

// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------

let selectedMapState = null; // the state with the outline, or null

function mapStep(count) {
	if (!count) return -1;
	return MAP_STEPS.findIndex((step) => count >= step.min && count <= step.max);
}

// Mix two #rrggbb colors. weight 0 gives a, weight 1 gives b.
function mixColors(a, b, weight) {
	const parse = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
	const ca = parse(a);
	const cb = parse(b);
	return "#" + ca.map((v, i) => Math.round(v + (cb[i] - v) * weight).toString(16).padStart(2, "0")).join("");
}

// WCAG relative luminance of a #rrggbb color.
function luminance(hex) {
	const [r, g, b] = [1, 3, 5].map((i) => {
		const v = parseInt(hex.slice(i, i + 2), 16) / 255;
		return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
	});
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(a, b) {
	const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
	return (hi + 0.05) / (lo + 0.05);
}

// Pick the label color (light or dark) that reads better on a fill.
function labelColorFor(fill, light, dark) {
	return contrastRatio(fill, light) >= contrastRatio(fill, dark) ? light : dark;
}

// Write the theme colors and the state colors into the simplemaps settings.
function applyMapColors() {
	const data = simplemaps_usmap_mapdata;
	const ink = cssVar("--ink");
	const none = cssVar("--map-none");
	const labelLight = cssVar("--map-label-light");
	const labelDark = cssVar("--map-label-dark");

	data.main_settings.border_color = cssVar("--map-border");
	data.main_settings.state_color = none;
	data.main_settings.state_hover_color = mixColors(none, ink, 0.2);
	data.labels = data.labels || {};

	Object.keys(data.state_specific).forEach((code) => {
		const step = mapStep(banCount(code));
		const color = step < 0 ? none : cssVar(MAP_STEPS[step].cssVar);
		const spec = data.state_specific[code];
		spec.color = color;
		spec.hover_color = mixColors(color, ink, 0.25);
		spec.description = "default";
		// Light label text on dark fills and dark text on light fills.
		const labelColor = step < 0 ? cssVar("--ink-2") : labelColorFor(color, labelLight, labelDark);
		data.labels[code] = Object.assign(data.labels[code] || {}, { color: labelColor, hover_color: labelColor });
	});
}

function showMapTip(code, anchorEvent, anchorNode) {
	const tip = byId("map-tip");
	const name = knownStateName(code) || code;
	const n = banCount(code);
	tip.replaceChildren(el("strong", null, name), document.createTextNode(siteIndex ? (n ? ": " + formatNumber(n) + " ban events" : ": no recorded bans") : ""));
	tip.hidden = false;
	positionMapTip(anchorEvent, anchorNode);
}

// Put the tip above the pointer, or above the center of the state when a key press focused it.
function positionMapTip(anchorEvent, anchorNode) {
	const tip = byId("map-tip");
	const box = tip.parentElement.getBoundingClientRect();
	let x;
	let y;
	if (anchorEvent && anchorEvent.clientX !== undefined) {
		x = anchorEvent.clientX - box.left;
		y = anchorEvent.clientY - box.top;
	} else if (anchorNode) {
		const r = anchorNode.getBoundingClientRect();
		x = r.left + r.width / 2 - box.left;
		y = r.top + r.height / 2 - box.top;
	} else return;
	// Keep the tip inside the map box.
	const half = tip.offsetWidth / 2;
	tip.style.left = Math.max(half, Math.min(box.width - half, x)) + "px";
	tip.style.top = y + "px";
}

function hideMapTip() {
	byId("map-tip").hidden = true;
}

// Outlines sit in the map SVG as extra paths that copy the shape of a state. They have
// no fill and ignore the mouse. The page never moves a state element itself, because moving
// a focused element in the DOM would drop the keyboard focus and cancel a mouse click.
const outlines = {}; // kind ("selected" or "focus") -> SVG path

function drawOutline(kind, code, color, width) {
	const states = window.simplemaps_usmap && simplemaps_usmap.states;
	let path = outlines[kind];
	const node = code && states && states[code] ? states[code].node : null;
	if (!node) {
		if (path) path.setAttribute("d", "");
		return;
	}
	if (!path || !path.isConnected) {
		path = document.createElementNS("http://www.w3.org/2000/svg", "path");
		path.setAttribute("fill", "none");
		path.setAttribute("pointer-events", "none");
		path.setAttribute("stroke-linejoin", "round");
		outlines[kind] = path;
	}
	node.parentNode.appendChild(path); // last child, so it draws on top
	path.setAttribute("d", node.getAttribute("d"));
	if (node.getAttribute("transform")) path.setAttribute("transform", node.getAttribute("transform"));
	path.setAttribute("stroke", color);
	path.setAttribute("stroke-width", width);
}

// Outline the state of the current route. Pass null to remove the outline.
function highlightMapState(code) {
	selectedMapState = code;
	if (window.simplemaps_usmap && simplemaps_usmap.loaded) drawOutline("selected", code, cssVar("--map-outline"), 3);
}

// Make each state reachable and readable with a keyboard and a screen reader.
function decorateMapStates() {
	const states = simplemaps_usmap.states;
	Object.keys(states).forEach((code) => {
		const spec = simplemaps_usmap_mapdata.state_specific[code];
		if (!spec || spec.hide === "yes" || !states[code].node) return;
		const node = states[code].node;
		const n = banCount(code);
		node.setAttribute("tabindex", "0");
		node.setAttribute("role", "button");
		node.setAttribute("aria-label", (knownStateName(code) || code) + ", " + (n ? formatNumber(n) + " ban events" : "no recorded bans"));
		node.addEventListener("focus", () => {
			// A mouse press also focuses the node. Only a keyboard focus draws the outline and the tip.
			if (!node.matches(":focus-visible")) return;
			showMapTip(code, null, node);
			drawOutline("focus", code, cssVar("--focus"), 4);
		});
		node.addEventListener("blur", () => {
			hideMapTip();
			drawOutline("focus", null);
		});
		node.addEventListener("keydown", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				navigate("state=" + code);
			}
		});
	});
}

function buildLegend() {
	const legend = byId("map-legend");
	legend.replaceChildren();
	legend.appendChild(el("span", "legend-title", "Ban events:"));
	const addItem = (color, label) => {
		const item = el("span", "legend-item");
		const swatch = el("span", "legend-swatch");
		swatch.style.background = "var(" + color + ")";
		item.append(swatch, document.createTextNode(label));
		legend.appendChild(item);
	};
	addItem("--map-none", "None recorded");
	MAP_STEPS.forEach((step) => addItem(step.cssVar, step.label));
}

let mapStarted = false;

// Start the map once. The map needs a visible box to measure, so it waits for the landing page.
function ensureMap() {
	if (mapStarted || !siteIndexSettled || byId("landing").hidden) return;
	mapStarted = true;
	initMap();
}

function initMap() {
	const map = simplemaps_usmap;
	map.hooks.click_state = (code) => navigate("state=" + code);
	map.hooks.over_state = (code) => showMapTip(code, window.lastMapPointer, null);
	map.hooks.out_state = () => hideMapTip();
	map.hooks.complete = () => {
		decorateMapStates();
		highlightMapState(selectedMapState);
	};
	byId("map").addEventListener("mousemove", (event) => {
		window.lastMapPointer = event;
		if (!byId("map-tip").hidden) positionMapTip(event, null);
	});
	applyMapColors();
	map.load();

	// Repaint when the person switches between light and dark mode.
	window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
		applyMapColors();
		map.refresh();
		setTimeout(() => highlightMapState(selectedMapState), 0); // the outline color depends on the theme
	});
}

// ---------------------------------------------------------------------------
// Routes: #zip=NNNNN, #state=XX, or nothing
// ---------------------------------------------------------------------------

// Read location.hash. Return {kind: "zip" | "state" | "home", ...}.
function parseRoute() {
	const params = new URLSearchParams(location.hash.replace(/^#/, ""));
	const zip = params.get("zip");
	const state = params.get("state");
	if (zip !== null) return /^\d{5}$/.test(zip) ? { kind: "zip", zip: zip } : { kind: "home", badZip: zip };
	if (state !== null) return /^[A-Za-z]{2}$/.test(state) ? { kind: "state", code: state.toUpperCase() } : { kind: "home" };
	return { kind: "home" };
}

// Go to a route such as "zip=75001". An empty string goes to the landing page.
// A change of hash adds a history entry, so the back button works.
function navigate(hash) {
	const target = hash ? "#" + hash : "";
	if (location.hash === target || (!hash && !location.hash)) {
		route(); // the hash does not change, so no hashchange event comes
	} else if (hash) {
		location.hash = hash; // fires hashchange, which calls route()
	} else {
		history.pushState(null, "", location.pathname + location.search); // clears the hash and adds a history entry
		route();
	}
}

window.addEventListener("hashchange", route);

// Show whatever the URL asks for.
function route() {
	routeToken += 1;
	const token = routeToken;
	const current = parseRoute();
	filters = defaultFilters();
	if (current.kind === "home") return showHome(current);
	if (current.kind === "zip") return showZip(current.zip, token);
	return showState(current.code, token);
}

function setPage(name) {
	byId("landing").hidden = name !== "home";
	byId("results").hidden = name === "home";
}

function showHome(current) {
	setPage("home");
	disconnectSpy();
	view = null;
	filterUi = null;
	document.title = "Banned Books Near Me";
	byId("zip-error").textContent = current.badZip !== undefined ? "Enter a 5-digit ZIP code, for example 78217." : "";
	byId("state-select").value = "";
	window.scrollTo(0, 0);
	ensureMap();
}

// ---------------------------------------------------------------------------
// Loading a view
// ---------------------------------------------------------------------------

function isStale(token) {
	return token !== routeToken;
}

// Give each ban event a link to its state code and book, once per state file.
function prepareState(code, data) {
	if (data._prepared) return data;
	data._prepared = true;
	data.books = data.books || {};
	data.districts = data.districts || {};
	data.bans = data.bans || [];
	Object.entries(data.books).forEach(([id, book]) => {
		book._id = id;
		book._search = fold((book.title || "") + " " + (Array.isArray(book.authors) ? book.authors.join(" ") : ""));
	});
	data.bans.forEach((ban) => {
		ban._code = code;
		ban._book = data.books[ban.book] || { _id: ban.book, title: "Unknown title", authors: [], _search: "unknown title" };
	});
	return data;
}

function loadState(code) {
	return fetchJson("states/" + code + ".json").then((data) => prepareState(code, data));
}

function beginRoute(placeLabel) {
	setPage("results");
	disconnectSpy();
	view = null;
	filterUi = null;
	document.title = placeLabel + " | Banned Books Near Me";
	renderSearchBar(null);
	renderLoading();
	window.scrollTo(0, 0);
}

async function showState(code, token) {
	const name = knownStateName(code) || code;
	beginRoute(name);
	try {
		await indexPromise;
		if (isStale(token)) return;
		if (!knownStateName(code)) return renderNoData("We do not know the state " + code, "Pick a state from the list, or search by ZIP code.");
		let data = null;
		// index.json tells us which states have a file. This skips a request that would fail.
		if (siteIndex && siteIndex.states && !siteIndex.states[code]) data = null;
		else data = await loadState(code);
		if (isStale(token)) return;
		if (!data || !data.bans.length) {
			renderView(buildEmptyStateView(code));
			return;
		}
		renderView(buildStateView(code, data));
	} catch (error) {
		if (isStale(token)) return;
		if (error.status === 404) renderView(buildEmptyStateView(code));
		else renderLoadError(name, token, () => route());
	}
}

async function showZip(zip, token) {
	beginRoute("ZIP " + zip);
	const notFoundTitle = "We have no data for ZIP " + zip;
	const notFoundText = "Check the number, or pick your state instead.";
	try {
		await indexPromise;
		const zipFile = await fetchJson("zips/" + zip.slice(0, 3) + ".json");
		if (isStale(token)) return;
		if (!Object.prototype.hasOwnProperty.call(zipFile, zip)) return renderNoData(notFoundTitle, notFoundText);
		const entry = zipFile[zip];
		const codes = entry.states || [];
		if (!codes.length) return renderNoData(notFoundTitle, notFoundText);
		const files = await Promise.all(codes.map((code) => loadState(code).catch((e) => (e.status === 404 ? null : Promise.reject(e)))));
		if (isStale(token)) return;
		const states = codes.map((code, i) => ({ code: code, name: stateName(code, files[i]), data: files[i] || prepareState(code, { name: knownStateName(code) || code }) }));
		renderView(buildZipView(zip, entry, states));
	} catch (error) {
		if (isStale(token)) return;
		if (error.status === 404) renderNoData(notFoundTitle, notFoundText);
		else renderLoadError("ZIP " + zip, token, () => route());
	}
}

// ---------------------------------------------------------------------------
// Building a view: sections made of ban events
// ---------------------------------------------------------------------------

function sectionSpec(id, group, title, events, options) {
	return Object.assign({ id: id, group: group, title: title, events: events, statewide: false, showPlaces: false }, options);
}

function districtName(data, key) {
	const d = data.districts && data.districts[key];
	return d && d.name ? d.name : "Unknown district";
}

// State route: statewide bans first, then every district ban.
function buildStateView(code, data) {
	const name = stateName(code, data);
	const statewide = data.bans.filter((b) => b.scope === "statewide");
	const district = data.bans.filter((b) => b.scope !== "statewide");
	return {
		kind: "state",
		place: name,
		sub: "Every ban event recorded in " + name + ".",
		states: [{ code: code, name: name, data: data }],
		sections: [
			sectionSpec("statewide", "statewide", "Statewide bans in " + name, statewide, {
				statewide: true,
				tally: "banned statewide in " + name,
				intro: "A statewide ban applies in every district of " + name + ".",
				emptyText: "No statewide bans recorded for " + name + ".",
			}),
			sectionSpec("districts", "districts", "District bans in " + name, district, {
				showPlaces: true,
				tally: "banned by districts in " + name,
				emptyText: "No district bans recorded for " + name + ".",
			}),
		],
		nav: [
			{ key: "top", label: "Top districts", target: "top-districts" },
			{ key: "statewide", label: "Statewide", sections: ["statewide"] },
			{ key: "districts", label: "District bans", sections: ["districts"] },
			{ key: "unbanned", label: "Books Unbanned", target: "unbanned" },
		],
		topDistricts: { code: code, data: data },
		libraries: null,
		links: [trackerLink(name)],
	};
}

// A state with no ban events: a short view with a plain message.
function buildEmptyStateView(code) {
	const name = knownStateName(code) || code;
	return {
		kind: "empty-state",
		place: name,
		sub: "",
		states: [{ code: code, name: name, data: prepareState(code, { name: name }) }],
		sections: [],
		nav: [],
		emptyTitle: "No bans recorded in " + name,
		emptyText: "The sources we use list no school book bans in " + name + ". That can also mean the state has less reporting.",
		libraries: null,
		links: [trackerLink(name)],
	};
}

// ZIP route: one section per district, then statewide bans, then the rest of the state.
function buildZipView(zip, entry, states) {
	const sections = [];
	const nav = [];
	const districtIds = [];
	const statewideIds = [];
	const restIds = [];
	const multi = states.length > 1;
	const placeNames = states.map((s) => s.name);

	states.forEach(({ code, name, data }) => {
		const shown = new Set(); // books already shown above, so "Other bans" leaves them out
		const remember = (events) => events.forEach((b) => shown.add(b.book));

		(entry.districts || [])
			.filter((d) => !d.state || d.state === code)
			.forEach((d) => {
				const events = data.bans.filter((b) => b.district === d.leaid);
				remember(events);
				const id = "district-" + code + "-" + d.leaid;
				districtIds.push(id);
				sections.push(
					sectionSpec(id, "districts", "Bans in " + d.name, events, {
						tally: "banned in " + d.name,
						emptyText: "No bans recorded in " + d.name + ".",
						emptyIsNotice: true,
						stateCode: code,
					}),
				);
			});

		const statewide = data.bans.filter((b) => b.scope === "statewide");
		remember(statewide);
		const swId = "statewide-" + code;
		statewideIds.push(swId);
		sections.push(
			sectionSpec(swId, "statewide", "Statewide bans in " + name, statewide, {
				statewide: true,
				tally: "banned statewide in " + name,
				intro: "A statewide ban applies in every district of " + name + ".",
				emptyText: "No statewide bans recorded for " + name + ".",
				stateCode: code,
			}),
		);

		const other = data.bans.filter((b) => !shown.has(b.book));
		const restId = "rest-" + code;
		restIds.push(restId);
		sections.push(
			sectionSpec(restId, "rest", "Rest of " + name, other, {
				showPlaces: true,
				tally: "banned elsewhere in " + name,
				intro: "Books banned in other districts of " + name + ". Books shown above are left out.",
				emptyText: "No other bans recorded for " + name + ".",
				stateCode: code,
			}),
		);
	});

	if (districtIds.length) nav.push({ key: "districts", label: districtIds.length === 1 ? "Your district" : "Your districts", sections: districtIds });
	nav.push({ key: "statewide", label: "Statewide", sections: statewideIds });
	nav.push({ key: "rest", label: multi ? "Rest of the states" : "Rest of " + placeNames[0], sections: restIds });
	nav.push({ key: "libraries", label: "Nearby libraries", target: "libraries" });
	nav.push({ key: "unbanned", label: "Books Unbanned", target: "unbanned" });

	const districtNames = (entry.districts || []).map((d) => d.name);
	return {
		kind: "zip",
		zip: zip,
		place: "ZIP " + zip + ", " + placeNames.join(" and "),
		sub: districtNames.length ? "School districts: " + districtNames.join("; ") + "." : "No school district is listed for this ZIP code.",
		states: states,
		sections: sections,
		nav: nav,
		libraries: entry.libraries || [],
		links: states.map((s) => trackerLink(s.name)),
	};
}

function trackerLink(name) {
	const p = el("p", "results-link");
	p.appendChild(makeLink(EVERYLIBRARY_URL, "Track book-ban bills in " + name + " on EveryLibrary"));
	return p;
}

// ---------------------------------------------------------------------------
// Filters and sorting
// ---------------------------------------------------------------------------

function defaultFilters() {
	return { q: "", statuses: new Set(), year: "", sort: "az", district: "" };
}

function filtersActive() {
	return Boolean(filters.q || filters.statuses.size || filters.year || filters.district);
}

// Does one ban event pass the status, year, and district filters? (The search box tests books, see bookPasses.)
function eventPasses(event, ignoreDistrict) {
	if (filters.statuses.size && !filters.statuses.has(event.status)) return false;
	if (filters.year && (event.year || NO_YEAR) !== filters.year) return false;
	if (!ignoreDistrict && filters.district && event.district !== filters.district) return false;
	return true;
}

// Every word in the search box must appear in the title or the authors.
function bookPasses(book, tokens) {
	return tokens.every((t) => book._search.includes(t));
}

function searchTokens() {
	return fold(filters.q).split(/\s+/).filter(Boolean);
}

// Turn ban events into one group per book. Apply the filters. Sort the groups.
function makeGroups(events) {
	const tokens = searchTokens();
	const groups = new Map();
	events.forEach((event) => {
		if (!eventPasses(event, false) || !bookPasses(event._book, tokens)) return;
		const key = event._code + ":" + event.book;
		let group = groups.get(key);
		if (!group) {
			group = { key: key, code: event._code, bookId: event.book, book: event._book, events: [], districts: new Set(), statewide: false, latest: "", worst: STATUS_ORDER.length };
			groups.set(key, group);
		}
		group.events.push(event);
		if (event.scope === "statewide") group.statewide = true;
		else if (event.district) group.districts.add(event.district);
		if ((event.year || "") > group.latest) group.latest = event.year || "";
		const rank = STATUS_ORDER.indexOf(event.status);
		if (rank >= 0 && rank < group.worst) group.worst = rank;
	});
	const list = [...groups.values()];
	const byTitle = (a, b) => collator.compare(a.book.title, b.book.title);
	const places = (g) => g.districts.size + (g.statewide ? 1 : 0);
	if (filters.sort === "new") list.sort((a, b) => (a.latest < b.latest ? 1 : a.latest > b.latest ? -1 : byTitle(a, b)));
	else if (filters.sort === "places") list.sort((a, b) => places(b) - places(a) || byTitle(a, b));
	else list.sort(byTitle);
	return list;
}

// The number of books in a section before any filter. This is the "of M" in "N of M books".
function baseCount(section) {
	if (section.base === undefined) section.base = new Set(section.events.map((e) => e._code + ":" + e.book)).size;
	return section.base;
}

// Compute the groups for every section with the current filters, then redraw all parts that show counts.
function applyFilters() {
	view.sections.forEach((section) => {
		section.groups = makeGroups(section.events);
	});
	updateFilterUi();
	renderTally();
	renderNav();
	view.sections.forEach(renderSectionBody);
	renderTopDistricts();
}

// ---------------------------------------------------------------------------
// Rendering a view
// ---------------------------------------------------------------------------

function results() {
	return byId("results-body");
}

// The top bar of the results page: a way back, and a way to search again.
function renderSearchBar(currentState) {
	const bar = byId("results-search");
	bar.replaceChildren();
	const back = el("a", "back-link", "← All states");
	back.href = "./";
	back.addEventListener("click", (event) => {
		event.preventDefault();
		navigate("");
	});
	const form = el("form", "mini-form js-zip-form");
	form.noValidate = true;
	const label = el("label", "sr-only", "Search by ZIP code");
	label.htmlFor = "mini-zip";
	const input = el("input", "input");
	input.id = "mini-zip";
	input.name = "zip";
	input.type = "text";
	input.inputMode = "numeric";
	input.maxLength = 5;
	input.autocomplete = "postal-code";
	input.placeholder = "ZIP code";
	const go = el("button", "btn", "Search ZIP");
	go.type = "submit";
	const error = el("p", "field-error sr-only");
	error.setAttribute("role", "alert");
	form.append(label, input, go, error);
	const stateLabel = el("label", "sr-only", "Pick a state");
	stateLabel.htmlFor = "mini-state";
	const select = el("select", "select js-state-select");
	select.id = "mini-state";
	select.style.width = "13rem";
	fillStateSelect(select);
	wireStateSelect(select);
	if (currentState) select.value = currentState;
	bar.append(back, form, stateLabel, select);
}

function renderLoading() {
	const body = results();
	body.setAttribute("aria-busy", "true");
	const grid = el("div", "cards");
	for (let i = 0; i < 12; i++) grid.appendChild(el("div", "skel skel-card"));
	const wrap = el("div");
	wrap.setAttribute("role", "status");
	wrap.appendChild(el("span", "sr-only", "Loading results"));
	wrap.append(el("div", "skel skel-title"), el("div", "skel skel-line"), el("div", "skel skel-line"), grid);
	body.replaceChildren(wrap);
}

function renderNoData(title, text) {
	results().setAttribute("aria-busy", "false");
	const panel = el("div", "panel");
	panel.setAttribute("role", "status");
	const heading = el("h1", null, title);
	heading.tabIndex = -1;
	panel.append(heading, el("p", "muted", text));
	const actions = el("div", "panel-actions");
	const home = el("a", "btn btn-primary", "Back to the map");
	home.href = "./";
	home.addEventListener("click", (event) => {
		event.preventDefault();
		navigate("");
	});
	actions.appendChild(home);
	panel.appendChild(actions);
	results().replaceChildren(panel);
	heading.focus({ preventScroll: true });
}

function renderLoadError(place, token, retry) {
	results().setAttribute("aria-busy", "false");
	const panel = el("div", "panel");
	panel.setAttribute("role", "alert");
	const heading = el("h1", null, "We could not load the data for " + place);
	heading.tabIndex = -1;
	panel.append(heading, el("p", "muted", "The page could not reach the data files. Check your connection and try again."));
	const actions = el("div", "panel-actions");
	const again = el("button", "btn btn-primary", "Try again");
	again.type = "button";
	again.addEventListener("click", retry);
	actions.appendChild(again);
	panel.appendChild(actions);
	results().replaceChildren(panel);
	heading.focus({ preventScroll: true });
}

function renderView(newView) {
	view = newView;
	const currentState = view.kind === "state" || view.kind === "empty-state" ? view.states[0].code : "";
	renderSearchBar(currentState);
	highlightMapState(view.states.length ? view.states[0].code : null);

	const body = results();
	body.setAttribute("aria-busy", "false");

	// Header: place, one-line description, one tally line per section, links.
	const head = el("header", "results-head");
	const title = el("h1", "results-title", view.place);
	title.tabIndex = -1;
	head.appendChild(title);
	if (view.sub) head.appendChild(el("p", "results-sub", view.sub));
	if (view.sections.length) {
		view.tallyList = el("ul", "tally");
		head.appendChild(view.tallyList);
	}
	view.links.forEach((link) => head.appendChild(link));
	body.replaceChildren(head);

	if (view.kind === "empty-state") {
		const panel = el("div", "panel");
		panel.appendChild(el("h2", null, view.emptyTitle));
		panel.appendChild(el("p", "muted", view.emptyText));
		body.appendChild(panel);
		title.focus({ preventScroll: true });
		return;
	}

	// Section bar, filters, sections, libraries.
	view.navEl = el("nav", "section-bar");
	view.navEl.setAttribute("aria-label", "Sections of this page");
	body.appendChild(view.navEl);
	body.appendChild(buildFilters());

	if (view.topDistricts) {
		view.topEl = buildTopDistrictsShell();
		body.appendChild(view.topEl);
	}

	view.sections.forEach((section) => {
		section.el = buildSectionShell(section);
		body.appendChild(section.el);
	});
	if (view.libraries) body.appendChild(buildLibraries(view));

	applyFilters();
	observeSections();
	title.focus({ preventScroll: true });
}

// --- tally: one line per section in the header ----------------------------

function renderTally() {
	if (!view.tallyList) return;
	const active = filtersActive();
	const lines = view.sections.map((section) => {
		const n = section.groups.length;
		const total = baseCount(section);
		const item = el("li");
		item.appendChild(el("span", "tally-num", formatNumber(n)));
		item.appendChild(el("span", "tally-label", plural(n, "book") + " " + section.tally));
		if (active && n !== total) item.appendChild(el("span", "tally-sub", "of " + formatNumber(total) + " before filters"));
		else if (section.events.length) item.appendChild(el("span", "tally-sub", formatNumber(section.events.length) + " ban " + plural(section.events.length, "event")));
		return item;
	});
	view.tallyList.replaceChildren(...lines);
}

// --- section bar ---------------------------------------------------------

function navTarget(item) {
	if (item.target) return byId(item.target);
	const first = view.sections.find((s) => s.id === item.sections[0]);
	return first ? first.el : null;
}

function renderNav() {
	const list = el("ul");
	view.nav.forEach((item) => {
		const li = el("li");
		const button = el("button", "section-link", item.label);
		button.type = "button";
		button.dataset.key = item.key;
		if (item.sections) {
			const count = item.sections.reduce((sum, id) => sum + view.sections.find((s) => s.id === id).groups.length, 0);
			button.appendChild(el("span", "count", formatNumber(count)));
		}
		button.addEventListener("click", () => {
			const target = navTarget(item);
			if (!target) return;
			target.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
			target.focus({ preventScroll: true });
		});
		li.appendChild(button);
		list.appendChild(li);
	});
	view.navEl.replaceChildren(list);
	if (view.activeKey) markNav(view.activeKey);
}

function markNav(key) {
	view.activeKey = key;
	view.navEl.querySelectorAll(".section-link").forEach((button) => {
		if (button.dataset.key === key) button.setAttribute("aria-current", "true");
		else button.removeAttribute("aria-current");
	});
}

function disconnectSpy() {
	if (spy) spy.disconnect();
	spy = null;
}

// Mark the section bar entry whose section is near the top of the screen.
function observeSections() {
	disconnectSpy();
	if (!("IntersectionObserver" in window)) return;
	const targets = new Map(); // element -> nav key
	view.nav.forEach((item) => {
		const target = navTarget(item);
		if (target) targets.set(target, item.key);
	});
	const visible = new Set();
	spy = new IntersectionObserver(
		(entries) => {
			entries.forEach((entry) => (entry.isIntersecting ? visible.add(entry.target) : visible.delete(entry.target)));
			const first = [...targets.keys()].find((node) => visible.has(node));
			if (first) markNav(targets.get(first));
		},
		{ rootMargin: "-80px 0px -55% 0px" },
	);
	targets.forEach((key, node) => spy.observe(node));
}

// --- filter controls -----------------------------------------------------

function buildFilters() {
	const ui = {};
	const box = el("div", "filters");
	box.setAttribute("role", "search");
	box.setAttribute("aria-label", "Filter and sort the books");
	const body = el("div", "filters-body");
	body.id = "filters-body";

	// On a phone this button opens and closes the fields. At 760 px and wider the CSS hides it.
	ui.toggle = el("button", "btn btn-quiet filters-toggle");
	ui.toggle.type = "button";
	ui.toggle.setAttribute("aria-controls", body.id);
	ui.toggle.setAttribute("aria-expanded", "false");
	ui.toggle.addEventListener("click", () => {
		const open = box.classList.toggle("is-open");
		ui.toggle.setAttribute("aria-expanded", String(open));
	});

	// Search
	const searchField = el("div", "field");
	const searchLabel = el("label", null, "Search by title or author");
	searchLabel.htmlFor = "filter-q";
	ui.q = el("input", "input");
	ui.q.id = "filter-q";
	ui.q.type = "search";
	ui.q.autocomplete = "off";
	ui.q.placeholder = "For example: Maus";
	let timer = null;
	ui.q.addEventListener("input", () => {
		clearTimeout(timer);
		timer = setTimeout(() => {
			filters.q = ui.q.value.trim();
			applyFilters();
		}, SEARCH_DELAY_MS);
	});
	searchField.append(searchLabel, ui.q);

	// School year
	const yearField = el("div", "field");
	const yearLabel = el("label", null, "School year");
	yearLabel.htmlFor = "filter-year";
	ui.year = el("select", "select");
	ui.year.id = "filter-year";
	const years = new Set();
	view.sections.forEach((s) => s.events.forEach((e) => years.add(e.year || NO_YEAR)));
	const allYears = el("option", null, "All school years");
	allYears.value = "";
	ui.year.appendChild(allYears);
	[...years]
		.sort()
		.reverse()
		.forEach((year) => {
			const option = el("option", null, year === NO_YEAR ? "Year not given" : year);
			option.value = year;
			ui.year.appendChild(option);
		});
	ui.year.addEventListener("change", () => {
		filters.year = ui.year.value;
		applyFilters();
	});
	yearField.append(yearLabel, ui.year);

	// Sort
	const sortField = el("div", "field");
	const sortLabel = el("label", null, "Sort by");
	sortLabel.htmlFor = "filter-sort";
	ui.sort = el("select", "select");
	ui.sort.id = "filter-sort";
	[
		["az", "Title, A to Z"],
		["new", "Newest school year"],
		["places", "Most places that banned it"],
	].forEach(([value, text]) => {
		const option = el("option", null, text);
		option.value = value;
		ui.sort.appendChild(option);
	});
	ui.sort.addEventListener("change", () => {
		filters.sort = ui.sort.value;
		applyFilters();
	});
	sortField.append(sortLabel, ui.sort);

	// Status chips
	const statusField = el("div", "field field-wide");
	statusField.appendChild(el("span", "field-label", "Status"));
	const chips = el("div", "chips");
	chips.setAttribute("role", "group");
	chips.setAttribute("aria-label", "Status");
	ui.chips = {};
	STATUS_ORDER.forEach((status) => {
		const chip = el("button", "chip");
		chip.type = "button";
		chip.append(buildMark(status), document.createTextNode(STATUS_LABELS[status]));
		chip.setAttribute("aria-pressed", "false");
		chip.addEventListener("click", () => {
			if (filters.statuses.has(status)) filters.statuses.delete(status);
			else filters.statuses.add(status);
			applyFilters();
		});
		ui.chips[status] = chip;
		chips.appendChild(chip);
	});
	ui.districtChip = el("span");
	chips.appendChild(ui.districtChip);
	statusField.appendChild(chips);

	// Footer: count of matching books and a clear button
	const foot = el("div", "filter-foot");
	ui.count = el("p", "filter-count");
	ui.count.setAttribute("aria-live", "polite");
	ui.clear = el("button", "linkish", "Clear all filters");
	ui.clear.type = "button";
	ui.clear.addEventListener("click", clearFilters);
	foot.append(ui.count, ui.clear);

	body.append(searchField, yearField, sortField, statusField);
	box.append(ui.toggle, body, foot);
	filterUi = ui;
	return box;
}

function clearFilters() {
	filters = defaultFilters();
	filterUi.q.value = "";
	filterUi.year.value = "";
	filterUi.sort.value = "az";
	applyFilters();
}

// Keep the controls and the match count in step with the filters.
function updateFilterUi() {
	const ui = filterUi;
	STATUS_ORDER.forEach((status) => ui.chips[status].setAttribute("aria-pressed", String(filters.statuses.has(status))));

	ui.districtChip.replaceChildren();
	if (filters.district) {
		const data = view.states[0].data;
		const chip = el("button", "chip chip-district", "District: " + districtName(data, filters.district) + " ×");
		chip.type = "button";
		chip.setAttribute("aria-label", "Remove the district filter: " + districtName(data, filters.district));
		chip.addEventListener("click", () => {
			filters.district = "";
			applyFilters();
		});
		ui.districtChip.appendChild(chip);
	}

	const seen = new Set();
	const all = new Set();
	view.sections.forEach((s) => {
		s.groups.forEach((g) => seen.add(g.key));
		s.events.forEach((e) => all.add(e._code + ":" + e.book));
	});
	ui.count.textContent = filtersActive() ? formatNumber(seen.size) + " of " + formatNumber(all.size) + " books match" : formatNumber(all.size) + " " + plural(all.size, "book");
	ui.clear.hidden = !filtersActive();
	const active = (filters.q ? 1 : 0) + filters.statuses.size + (filters.year ? 1 : 0) + (filters.district ? 1 : 0);
	ui.toggle.textContent = "Filter and sort" + (active ? " (" + active + " active)" : "");
}

// --- sections ------------------------------------------------------------

// The fixed frame of one section. Its cards change with the filters, the frame does not.
function buildSectionShell(section) {
	const node = el("section", "block");
	node.id = "sec-" + section.id;
	node.tabIndex = -1;
	node.setAttribute("aria-labelledby", node.id + "-title");
	const head = el("div", "block-head");
	const title = el("h2", null, section.title);
	title.id = node.id + "-title";
	section.countEl = el("span", "block-count");
	head.append(title, section.countEl);
	node.appendChild(head);
	if (section.intro) node.appendChild(el("p", "block-sub", section.intro));
	section.bodyEl = el("div", "section-body");
	node.appendChild(section.bodyEl);
	return node;
}

// Draw the cards of one section. It shows PAGE_SIZE cards at first. "Show more" adds PAGE_SIZE more.
function renderSectionBody(section) {
	const total = baseCount(section);
	const count = section.groups.length;
	section.countEl.textContent = filtersActive() && count !== total ? formatNumber(count) + " of " + formatNumber(total) + " books" : formatNumber(count) + " " + plural(count, "book");

	if (!count) {
		const note = el("p", "empty-note");
		if (!total) {
			note.textContent = section.emptyText;
		} else {
			note.append("No books in this section match your filters. ");
			const clear = el("button", "linkish", "Clear all filters");
			clear.type = "button";
			clear.addEventListener("click", clearFilters);
			note.appendChild(clear);
		}
		section.bodyEl.replaceChildren(note);
		return;
	}

	const grid = el("div", "cards");
	const more = el("div", "more-wrap");
	const button = el("button", "btn");
	button.type = "button";
	more.appendChild(button);
	section.bodyEl.replaceChildren(grid, more);

	let shown = 0;
	const showNext = () => {
		const batch = section.groups.slice(shown, shown + PAGE_SIZE);
		batch.forEach((group) => grid.appendChild(buildCard(group, section)));
		shown += batch.length;
		const left = count - shown;
		more.hidden = left <= 0;
		button.textContent = "Show " + formatNumber(Math.min(PAGE_SIZE, left)) + " more (" + formatNumber(left) + " left)";
	};
	button.addEventListener("click", showNext);
	showNext();
}

// --- top districts (state route only) ------------------------------------

function buildTopDistrictsShell() {
	const node = el("section", "block");
	node.id = "top-districts";
	node.tabIndex = -1;
	node.setAttribute("aria-labelledby", "top-districts-title");
	const head = el("div", "block-head");
	const title = el("h2", null, "Districts with the most banned books");
	title.id = "top-districts-title";
	head.appendChild(title);
	node.appendChild(head);
	node.appendChild(el("p", "block-sub", "Click a district to see only its books. The other filters still apply."));
	view.topListEl = el("div");
	node.appendChild(view.topListEl);
	view.topShown = RANK_PAGE;
	return node;
}

// Count books per district. All filters count here except the district filter itself.
function renderTopDistricts() {
	if (!view.topDistricts) return;
	const data = view.topDistricts.data;
	const tokens = searchTokens();
	const books = new Map(); // district key -> Set of book IDs
	view.sections.forEach((section) => {
		if (section.statewide) return;
		section.events.forEach((event) => {
			if (!event.district || !eventPasses(event, true) || !bookPasses(event._book, tokens)) return;
			if (!books.has(event.district)) books.set(event.district, new Set());
			books.get(event.district).add(event.book);
		});
	});
	const ranked = [...books.entries()].map(([key, set]) => ({ key: key, name: districtName(data, key), n: set.size })).sort((a, b) => b.n - a.n || collator.compare(a.name, b.name));

	if (!ranked.length) {
		view.topListEl.replaceChildren(el("p", "empty-note", baseCount(view.sections[1]) ? "No districts match your filters." : "No district bans recorded."));
		return;
	}
	const max = ranked[0].n;
	const list = el("ul", "rank");
	ranked.slice(0, view.topShown).forEach((row) => {
		const li = el("li");
		const button = el("button", "rank-btn");
		button.type = "button";
		button.setAttribute("aria-pressed", String(filters.district === row.key));
		button.setAttribute("aria-label", row.name + ", " + row.n + " " + plural(row.n, "book") + ". Filter to this district.");
		const bar = el("span", "rank-bar");
		const fill = el("span");
		fill.style.width = Math.max(1, (row.n / max) * 100) + "%";
		bar.appendChild(fill);
		button.append(el("span", "rank-name", row.name), bar, el("span", "rank-num", formatNumber(row.n)));
		button.addEventListener("click", () => {
			filters.district = filters.district === row.key ? "" : row.key;
			applyFilters();
			if (filters.district) {
				const target = view.sections[1].el;
				target.scrollIntoView({ behavior: "auto", block: "start" });
			}
		});
		li.appendChild(button);
		list.appendChild(li);
	});
	const parts = [list];
	if (ranked.length > view.topShown) {
		const wrap = el("div", "more-wrap");
		const more = el("button", "btn", "Show " + Math.min(RANK_PAGE, ranked.length - view.topShown) + " more districts (" + (ranked.length - view.topShown) + " left)");
		more.type = "button";
		more.addEventListener("click", () => {
			view.topShown += RANK_PAGE;
			renderTopDistricts();
		});
		wrap.appendChild(more);
		parts.push(wrap);
	}
	view.topListEl.replaceChildren(...parts);
}

// ---------------------------------------------------------------------------
// Book cards and covers
// ---------------------------------------------------------------------------

// A small shape that repeats the status, so color is never the only signal.
function buildMark(status) {
	return el("span", "mark mark-" + status);
}

function buildBadge(status) {
	const known = STATUS_LABELS[status];
	const badge = el("span", "badge badge-" + (known ? status : "unknown"));
	if (known) badge.appendChild(buildMark(status));
	badge.appendChild(document.createTextNode(known || "Status unknown"));
	return badge;
}

// A number from 0 to 7, the same every time for one book ID.
function hashIndex(text) {
	let h = 5381;
	for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
	return h % 8;
}

// The authors, joined. A name that the data lists twice shows once.
function authorsText(book) {
	return Array.isArray(book.authors) ? [...new Set(book.authors)].join("; ") : "";
}

// A cover made of type: the title and author on a color picked from the book ID.
function buildPlaceholder(book) {
	const box = el("span", "cover cover-placeholder ph-" + hashIndex(String(book._id || book.title)));
	box.setAttribute("data-placeholder", "");
	box.appendChild(el("span", "ph-title", book.title));
	const authors = authorsText(book);
	if (authors) box.appendChild(el("span", "ph-author", authors));
	return box;
}

// A cover image. If the URL is missing, unsafe, or fails to load, show the placeholder.
// The browser loads the image only when it is near the screen (loading="lazy").
function buildCover(book) {
	const url = safeUrl(book.cover);
	if (!url) return buildPlaceholder(book);
	const frame = el("span", "cover");
	const img = el("img");
	img.alt = ""; // The card text already names the book, so the picture adds nothing for a screen reader.
	img.loading = "lazy";
	img.decoding = "async";
	img.width = 180;
	img.height = 270;
	const fallback = () => frame.replaceWith(buildPlaceholder(book));
	img.addEventListener("error", fallback, { once: true });
	// Open Library answers a missing cover with a 1 by 1 pixel picture. Treat it as no cover.
	img.addEventListener("load", () => img.naturalWidth <= 1 && fallback(), { once: true });
	img.src = url;
	frame.appendChild(img);
	return frame;
}

// One card for one book. The whole card is one button that opens the details dialog.
function buildCard(group, section) {
	const book = group.book;
	const card = el("button", "book-card");
	card.type = "button";
	card.setAttribute("aria-haspopup", "dialog");
	card.appendChild(buildCover(book));
	card.appendChild(el("span", "book-title", book.title));
	const authors = authorsText(book);
	if (authors) card.appendChild(el("span", "book-author", authors));

	const status = STATUS_ORDER[group.worst] || "unknown";
	const badgeWrap = el("span");
	badgeWrap.appendChild(buildBadge(status));
	card.appendChild(badgeWrap);

	const meta = [];
	if (group.latest) meta.push(group.latest);
	if (section.showPlaces && group.districts.size) meta.push(group.districts.size + " " + plural(group.districts.size, "district"));
	if (group.statewide && !section.statewide) meta.push("Statewide");
	if (meta.length) card.appendChild(el("span", "book-meta", meta.join(" · ")));

	card.addEventListener("click", () => openBookDialog(group));
	return card;
}

// ---------------------------------------------------------------------------
// Book details dialog
// ---------------------------------------------------------------------------

function sourceName(id) {
	const info = siteIndex && siteIndex.sources && siteIndex.sources[id];
	return info && info.name ? info.name : id;
}

// A source links to its per-ban url if it has one, else to the source's own page.
function buildSourceItem(source) {
	const info = siteIndex && siteIndex.sources && siteIndex.sources[source.id];
	const url = safeUrl(source.url) || (info ? safeUrl(info.url) : null);
	const item = el("li");
	item.appendChild(url ? makeLink(url, sourceName(source.id)) : el("span", null, sourceName(source.id)));
	return item;
}

// All ban events of one book in the states the page has loaded.
function eventsOfBook(bookId) {
	const found = [];
	view.states.forEach((st) => {
		if (!st.data._byBook) {
			st.data._byBook = new Map();
			st.data.bans.forEach((ban) => {
				if (!st.data._byBook.has(ban.book)) st.data._byBook.set(ban.book, []);
				st.data._byBook.get(ban.book).push(ban);
			});
		}
		(st.data._byBook.get(bookId) || []).forEach((ban) => found.push({ ban: ban, state: st }));
	});
	return found;
}

function buildEventItem(entry, multiState) {
	const { ban, state } = entry;
	const item = el("li", "event");
	const top = el("div", "event-top");
	const place = ban.scope === "statewide" ? "Statewide" : ban.district ? districtName(state.data, ban.district) : "District not named";
	top.appendChild(el("span", "event-place", multiState ? place + ", " + state.name : place));
	top.appendChild(buildBadge(ban.status));
	top.appendChild(el("span", "event-year", ban.year ? "School year " + ban.year : "School year not given"));
	item.appendChild(top);

	const sources = el("ul", "event-sources");
	(ban.sources || []).forEach((s) => sources.appendChild(buildSourceItem(s)));
	item.appendChild(sources);
	if (typeof ban.note === "string" && ban.note) item.appendChild(el("p", "event-note", "Note: " + ban.note));
	return item;
}

function buildBookLinks(book) {
	const links = el("div", "dialog-links");
	if (book.ol && /^OL\d+W$/.test(book.ol)) {
		links.appendChild(makeLink("https://openlibrary.org/works/" + encodeURIComponent(book.ol), "View on Open Library", "btn"));
		if (book.scan === true) {
			// TODO: link to https://openlibrary.org/borrow/ia/<ia_id> when state files carry the Internet Archive ID.
			// Until then, the work page shows the Borrow button.
			const ia = typeof book.ia === "string" && /^[A-Za-z0-9._-]+$/.test(book.ia) ? book.ia : null;
			const url = ia ? "https://openlibrary.org/borrow/ia/" + encodeURIComponent(ia) : "https://openlibrary.org/works/" + encodeURIComponent(book.ol);
			links.appendChild(makeLink(url, "Borrow a scan on Open Library", "btn"));
		}
	}
	const first = Array.isArray(book.authors) && book.authors.length ? book.authors[0] : "";
	const query = book.isbn ? "bn:" + book.isbn : (book.title + " " + first).trim();
	links.appendChild(makeLink("https://www.worldcat.org/search?q=" + encodeURIComponent(query), "Find in WorldCat", "btn"));
	return links;
}

function openBookDialog(group) {
	const book = group.book;
	const body = byId("book-dialog-body");
	body.replaceChildren();

	const head = el("div", "dialog-head");
	head.appendChild(buildCover(book));
	const text = el("div");
	const title = el("h2", null, book.title);
	title.id = "book-dialog-title";
	text.appendChild(title);
	const authors = authorsText(book);
	if (authors) text.appendChild(el("p", "dialog-authors", "by " + authors));
	text.appendChild(buildBookLinks(book));
	head.appendChild(text);
	body.appendChild(head);

	const entries = eventsOfBook(group.bookId).sort((a, b) => (b.ban.year || "").localeCompare(a.ban.year || "") || (a.ban.scope === "statewide" ? -1 : 1));
	const heading = el("h3", null, "Where it was banned (" + entries.length + " " + plural(entries.length, "ban event") + ")");
	body.appendChild(heading);
	const list = el("ul", "events");
	entries.forEach((entry) => list.appendChild(buildEventItem(entry, view.states.length > 1)));
	body.appendChild(list);

	const dialog = byId("book-dialog");
	document.body.classList.add("dialog-open");
	dialog.showModal();
	dialog.scrollTop = 0;
	byId("book-dialog-close").focus();
}

function wireDialog() {
	const dialog = byId("book-dialog");
	byId("book-dialog-close").addEventListener("click", () => dialog.close());
	// A click on the backdrop lands on the dialog element itself. A click inside lands on a child.
	dialog.addEventListener("click", (event) => {
		if (event.target === dialog) dialog.close();
	});
	dialog.addEventListener("close", () => document.body.classList.remove("dialog-open"));
}

// ---------------------------------------------------------------------------
// Nearest libraries (ZIP route only)
// ---------------------------------------------------------------------------

function buildLibraries(v) {
	const node = el("section", "block");
	node.id = "libraries";
	node.tabIndex = -1;
	node.setAttribute("aria-labelledby", "libraries-title");
	const head = el("div", "block-head");
	const title = el("h2", null, "Nearby libraries");
	title.id = "libraries-title";
	head.appendChild(title);
	node.appendChild(head);

	if (!v.libraries.length) {
		node.appendChild(el("p", "empty-note", "We have no library data for this ZIP code."));
		return node;
	}
	node.appendChild(el("p", "block-sub", "The " + Math.min(3, v.libraries.length) + " public libraries nearest to this ZIP code."));
	const grid = el("div", "lib-grid");
	v.libraries.slice(0, 3).forEach((lib) => grid.appendChild(buildLibraryCard(lib)));
	node.appendChild(grid);
	return node;
}

function buildLibraryCard(lib) {
	const card = el("div", "card");
	if (typeof lib.miles === "number") {
		const distance = el("div", "card-distance", lib.miles.toFixed(1));
		distance.appendChild(el("small", null, plural(lib.miles.toFixed(1) === "1.0" ? 1 : 2, "mile") + " away"));
		card.appendChild(distance);
	}
	card.appendChild(el("h3", null, lib.name));
	const cityLine = [lib.city, [lib.state, lib.zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
	const address = el("address");
	address.textContent = [lib.address, cityLine].filter(Boolean).join(", ");
	card.appendChild(address);
	const query = [lib.name, lib.address, cityLine].filter(Boolean).join(", ");
	card.appendChild(makeLink("https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(query), "Directions", "btn"));
	return card;
}

// ---------------------------------------------------------------------------
// Books Unbanned (loads at page start)
// ---------------------------------------------------------------------------

function buildProgramCard(program) {
	const card = el("div", "card");
	card.appendChild(el("h3", null, program.library));
	const quote = el("blockquote", null, "“" + program.eligibility + "”");
	card.appendChild(quote);
	const url = safeUrl(program.url);
	if (url) card.appendChild(makeLink(url, "Go to the program page", "btn btn-primary"));
	return card;
}

async function loadUnbanned() {
	const list = byId("unbanned-list");
	const checked = byId("unbanned-checked");
	try {
		const data = await fetchJson("books_unbanned.json");
		(data.programs || []).forEach((p) => list.appendChild(buildProgramCard(p)));
		checked.textContent = "Checked on " + formatDate(data.checked) + ". " + (data.note || "Check each library's page for current rules.");
	} catch (error) {
		checked.textContent = "The Books Unbanned programs could not be loaded. Please try again later.";
	}
}

// ---------------------------------------------------------------------------
// ZIP forms and start-up
// ---------------------------------------------------------------------------

// Both ZIP forms (landing and results bar) use this handler.
document.addEventListener("submit", (event) => {
	const form = event.target.closest(".js-zip-form");
	if (!form) return;
	event.preventDefault(); // Enter and the button both come here
	const input = form.elements.zip;
	const error = form.querySelector(".field-error");
	const zip = input.value.trim();
	if (!/^\d{5}$/.test(zip)) {
		error.textContent = "Enter a 5-digit ZIP code, for example 78217.";
		error.classList.remove("sr-only");
		input.setAttribute("aria-invalid", "true");
		input.focus();
		return;
	}
	error.textContent = "";
	input.removeAttribute("aria-invalid");
	navigate("zip=" + zip);
});

// Keep only digits in a ZIP box.
document.addEventListener("input", (event) => {
	if (event.target.matches && event.target.matches(".js-zip-form input[name=zip]")) {
		event.target.value = event.target.value.replace(/\D/g, "");
	}
});

// Links such as "Skip to main content" jump inside the page without changing the hash route.
document.addEventListener("click", (event) => {
	const link = event.target.closest && event.target.closest("[data-jump]");
	if (!link) return;
	event.preventDefault();
	const target = byId(link.dataset.jump);
	if (target) {
		target.scrollIntoView();
		target.focus();
	}
});

byId("brand-link").addEventListener("click", (event) => {
	event.preventDefault();
	navigate("");
});

async function start() {
	wireDialog();
	wireStateSelect(byId("state-select"));
	fillStateSelect(byId("state-select")); // names only until index.json arrives
	buildLegend();
	loadUnbanned();
	route(); // start loading the state or ZIP file in the URL at once
	await indexPromise;
	renderIndexParts();
	fillStateSelect(byId("state-select")); // add the counts
	byId("state-select").value = "";
	siteIndexSettled = true;
	ensureMap();
}

start();
