'use strict';

const PARTY_COLORS = {
  PNP:'#001ea0', PPD:'#e81f25', PIP:'#33a253', MVC:'#dda431',
  PD:'#00adc6', IND:'#6c757d', OTR:'#3f3f46', PPT:'#7c3aed',
  PPR:'#a855f7', MUS:'#d97706', EST:'#001ea0', SLA:'#dd4536',
  SI:'#001ea0', NO:'#946038', ELA:'#dd4536'
};
const IGNORED_PARTIES = new Set(['MVT','NVT','BLN','NUL','OVR','BAL','TOT']);
const RACE_NAMES = {
  GOB:'Gobernador', COM:'Comisionado Residente', SNA:'Senador por Acumulación',
  RPA:'Representante por Acumulación', SND:'Senador por Distrito',
  RPD:'Representante por Distrito', ALC:'Alcalde', PLE:'Plebiscito',
  'PLE-1':'Plebiscito · Pregunta 1', 'PLE-2':'Plebiscito · Pregunta 2'
};
const RACE_ORDER = ['GOB','COM','SNA','RPA','SND','RPD','ALC','PLE','PLE-1','PLE-2'];
const GEO_FILES = {precinto:'prekey', unidad:'unikey'};
const CENSUS_TOPICS = {
  housing:'Housing',
  population_and_household_denominators:'Population and households',
  education:'Education',
  income_and_earnings_related:'Income and earnings',
  poverty:'Poverty',
  government_aid_and_retirement:'Government aid and retirement'
};
const CENSUS_COLORS = ['#fff7bc','#fee391','#fec44f','#fe9929','#d95f0e'];
const CENSUS_DEFAULT_METRICS = {
  housing:'owner_occupied_pct_ratio',
  population_and_household_denominators:'B01003_001E',
  education:'bachelors_or_higher_pct_ratio',
  income_and_earnings_related:'income_100k_plus_pct_ratio',
  poverty:'below_poverty_pct_ratio',
  government_aid_and_retirement:'snap_pct_ratio'
};
const state = {
  year:2024, race:'GOB', geo:'municipio', mode:'totals', bundle:null,
  activeLayer:null, layers:{}, selectedMunicipio:null, selectedPrecinct:null,
  residencialesLayer:null, residencialesVisible:false, loading:false, request:0
};
const cache = new Map();
const byId = id => document.getElementById(id);
const numeric = value => typeof value === 'number' && Number.isFinite(value);
const map = L.map('map',{preferCanvas:true,attributionControl:false}).setView([18.2,-66.4],9);
const combined = {
  map:null,electionLayer:null,residencialesLayer:null,censusLayer:null,
  censusCatalog:null,censusCatalogYear:null,censusRequest:0,censusYear:2024,
  transparency:35,breaks:[]
};
const blockGroup = {
  map:null,dataLayer:null,geometryYear:null,catalog:null,catalogKey:null,
  request:0,statusMessage:'',year:2024,category:''
};
let activeTab='electoral';

map.createPane('residencialesPane');
map.getPane('residencialesPane').style.zIndex='640';
map.getPane('residencialesPane').style.pointerEvents='none';

function showError(message) {
  byId('error').textContent=message;
  byId('error').hidden=false;
  byId('status').textContent='No se pudo cargar el mapa.';
}

function clearError() {
  byId('error').hidden=true;
  byId('error').textContent='';
}

async function fetchJSON(path) {
  if(!cache.has(path)) {
    cache.set(path,(async()=>{
      const response=await fetch(path);
      if(!response.ok) throw new Error(`No se pudo cargar ${path} (${response.status}).`);
      return response.json();
    })());
  }
  return cache.get(path);
}

function normalizedName(value) {
  return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
    .toUpperCase().replace(/[^A-Z0-9]/g,'');
}

function targetId(feature) {
  return String(feature.properties.TARGET_ID ?? feature.properties.key ?? '');
}

function electionMetrics(catalog) {
  return catalog.metrics.filter(metric=>metric.topic==='election_returns');
}

function raceForMetric(metric) {
  const field=metric.id.split('__')[1];
  return field ? field.split('_')[0] : '';
}

function partyForMetric(metric) {
  const field=metric.id.split('__')[1]||'';
  const candidate=field.split('_').slice(1);
  return candidate[0]||'OTR';
}

function metricsByRace(metrics) {
  const groups=new Map();
  for(const metric of metrics) {
    const race=raceForMetric(metric);
    if(!race) continue;
    if(!groups.has(race)) groups.set(race,[]);
    groups.get(race).push(metric);
  }
  return groups;
}

function votesFromDirectData(metrics,data) {
  const rows=new Map(data.ids.map(id=>[String(id),{}]));
  for(const metric of metrics) {
    const race=raceForMetric(metric);
    const party=partyForMetric(metric);
    const values=data.values[metric.fields.direct];
    if(!Array.isArray(values)||values.length!==data.ids.length) {
      throw new Error(`El campo electoral ${metric.fields.direct} tiene un formato inválido.`);
    }
    data.ids.forEach((id,index)=>{
      const value=values[index];
      if(!numeric(value)) return;
      const byRace=rows.get(String(id));
      if(!byRace[race]) byRace[race]={};
      byRace[race][party]=(byRace[race][party]||0)+value;
    });
  }
  return rows;
}

function legacyVotesByPrekey(results,prekey) {
  return results.by_precinct[String(Number(prekey))]||{};
}

function legacyVotesByUnit(results,prekey,unit) {
  return results.by_unit[`${Number(prekey)}|${Number(unit)}`]||{};
}

function addVotes(target,source) {
  for(const [race,parties] of Object.entries(source||{})) {
    if(!target[race]) target[race]={};
    for(const [party,votes] of Object.entries(parties||{})) {
      if(numeric(votes)) target[race][party]=(target[race][party]||0)+votes;
    }
  }
}

function buildMunicipalVotes(prekeyGeo,prekeyVotes) {
  const byMunicipio=new Map();
  for(const feature of prekeyGeo.features) {
    const name=normalizedName(feature.properties.municipio);
    if(!byMunicipio.has(name)) byMunicipio.set(name,{});
    addVotes(byMunicipio.get(name),prekeyVotes.get(targetId(feature)));
  }
  return byMunicipio;
}

function partyVotesForFeature(feature) {
  if(state.geo==='municipio') {
    return state.bundle.municipalVotes.get(normalizedName(feature.properties.key||feature.properties.municipio))||{};
  }
  if(state.year==='2012') {
    if(state.geo==='precinto') {
      return legacyVotesByPrekey(state.bundle.legacy,feature.properties.PREKEY);
    }
    return legacyVotesByUnit(state.bundle.legacy,feature.properties.PREKEY,feature.properties.UNIKEY);
  }
  return state.bundle.geoVotes.get(targetId(feature))||{};
}

function eligibleVotes(votes) {
  return Object.entries(votes||{})
    .filter(([party,value])=>!IGNORED_PARTIES.has(party)&&numeric(value)&&value>=0);
}

function winnerFromVotes(votes) {
  return eligibleVotes(votes).sort((a,b)=>b[1]-a[1])[0]||null;
}

function raceMargin(votes) {
  const sorted=eligibleVotes(votes).map(([,value])=>value).filter(value=>value>0).sort((a,b)=>b-a);
  if(!sorted.length) return null;
  const total=sorted.reduce((sum,value)=>sum+value,0);
  if(total<=0) return null;
  return {votes:sorted[0]-(sorted[1]||0),percent:(sorted[0]-(sorted[1]||0))/total*100};
}

function partyColor(party) {
  if(state.race==='PLE'&&state.year===2024) {
    if(party==='EST') return PARTY_COLORS.PNP;
    if(party==='SLA') return PARTY_COLORS.SLA;
    if(party==='IND') return PARTY_COLORS.PIP;
  }
  if(state.race==='PLE'&&party==='IND') return PARTY_COLORS.PIP;
  if(state.race==='PLE-1'&&party==='NO') return '#94386c';
  if(state.race==='PLE-2'&&party==='EST') return PARTY_COLORS.PNP;
  if(state.race==='PLE-2'&&party==='IND') return PARTY_COLORS.PIP;
  return PARTY_COLORS[party]||'#475569';
}

function partyLabel(party) {
  if(state.race==='PLE'&&state.year===2024) {
    if(party==='EST') return 'Estadidad';
    if(party==='SLA') return 'Soberanía en Libre Asociación';
    if(party==='IND') return 'Independencia';
  }
  if(state.race==='PLE-2') {
    if(party==='ELA') return 'ELA';
    if(party==='EST') return 'Estadidad';
    if(party==='IND') return 'Independencia';
  }
  if(state.race==='PLE-1') {
    if(party==='SI') return 'Sí';
    if(party==='NO') return 'No';
  }
  return party;
}

function raceLabel() {
  return RACE_NAMES[state.race]||state.race;
}

function recordsForLayer() {
  const records=new Map();
  for(const feature of state.geoData.features) {
    const votes=partyVotesForFeature(feature)[state.race]||{};
    records.set(targetId(feature),votes);
  }
  return records;
}

function featureIsOutsideDrilldown(feature) {
  if(state.geo==='precinto'&&state.selectedMunicipio) {
    return normalizedName(feature.properties.municipio)!==state.selectedMunicipio;
  }
  if(state.geo==='unidad'&&state.selectedPrecinct) {
    const prekey=feature.properties.PREKEY??feature.properties.precinto;
    return String(Number(prekey))!==String(Number(state.selectedPrecinct));
  }
  if(state.geo==='unidad'&&state.selectedMunicipio) {
    return normalizedName(feature.properties.municipio)!==state.selectedMunicipio;
  }
  return false;
}

function featureStyle(feature) {
  if(featureIsOutsideDrilldown(feature)) {
    return {weight:.7,color:'#6b7280',fillOpacity:.22,fillColor:'#c9ced6'};
  }
  const votes=partyVotesForFeature(feature)[state.race];
  const winner=winnerFromVotes(votes);
  const margin=raceMargin(votes);
  let fillOpacity=winner?.75:.3;
  if(winner&&state.mode==='margin'&&margin) {
    const normalized=Math.max(0,Math.min(1,margin.percent/20));
    fillOpacity=.08+Math.pow(normalized,.65)*.9;
  }
  return {weight:.9,color:'#222',fillOpacity,fillColor:winner?partyColor(winner[0]):'#d5d5d5'};
}

function locationLabel(feature) {
  const props=feature.properties;
  const parts=[props.municipio];
  if(props.PREKEY||props.precinto) parts.push(`Precinto ${props.PREKEY||props.precinto}`);
  if(props.UNIKEY||props.unidad) parts.push(`Unidad ${props.UNIKEY||props.unidad}`);
  return parts.filter(Boolean).join(' · ');
}

function popupContent(feature) {
  const root=document.createElement('div');
  const place=document.createElement('strong');
  place.textContent=locationLabel(feature);
  root.append(place);
  const votes=partyVotesForFeature(feature)[state.race];
  if(!votes) {
    const missing=document.createElement('p');
    missing.textContent='No hay votos para esta área.';
    root.append(missing);
    return root;
  }
  const winner=winnerFromVotes(votes);
  if(winner) {
    const line=document.createElement('p');
    line.textContent=`Ganador: ${partyLabel(winner[0])} (${winner[1].toLocaleString()})`;
    root.append(line);
  }
  const margin=raceMargin(votes);
  if(margin) {
    const line=document.createElement('p');
    line.textContent=`Margen: ${margin.percent.toFixed(1)}%`;
    root.append(line);
  }
  for(const [party,value] of eligibleVotes(votes).sort((a,b)=>b[1]-a[1])) {
    const line=document.createElement('div');
    line.textContent=`${partyLabel(party)}: ${value.toLocaleString()}`;
    root.append(line);
  }
  return root;
}

function refreshLayerStyles() {
  [state.activeLayer,combined.electionLayer].filter(Boolean).forEach(layerGroup=>{
    layerGroup.eachLayer(layer=>{
      layer.setStyle(featureStyle(layer.feature));
      layer.setTooltipContent(locationLabel(layer.feature));
      layer.setPopupContent(popupContent(layer.feature));
    });
  });
  if(!state.bundle) return;
  renderNationwideTotals();
  renderLegend();
  if(combined.electionLayer)
    renderLegend('combined-election-legend','combined-election-note');
}

function onEachFeature(feature,layer) {
  layer.bindTooltip(locationLabel(feature),{sticky:true,opacity:.96,direction:'top'});
  layer.bindPopup(popupContent(feature),{autoPan:true});
  layer.on('mouseover',()=>layer.setStyle({weight:2.3}));
  layer.on('mouseout',()=>layer.setStyle({weight:.9}));
  layer.on('click',()=>{
    layer.setPopupContent(popupContent(feature)).openPopup();
    if(state.geo==='municipio') {
      state.selectedMunicipio=normalizedName(feature.properties.key||feature.properties.municipio);
      state.selectedPrecinct=null;
      changeGeography('precinto',layer.getBounds());
    } else if(state.geo==='precinto') {
      state.selectedPrecinct=feature.properties.PREKEY??feature.properties.precinto;
      changeGeography('unidad',layer.getBounds());
    }
  });
}

function setActiveScope(scope) {
  document.querySelectorAll('[data-scope]').forEach(button=>{
    button.classList.toggle('is-active',button.dataset.scope===scope);
  });
}

function drawCombinedElectoralLayer(bounds,fit=true) {
  if(!combined.map||!state.geoData) return;
  if(combined.electionLayer) combined.map.removeLayer(combined.electionLayer);
  combined.electionLayer=L.geoJSON(state.geoData,{style:featureStyle,onEachFeature}).addTo(combined.map);
  if(state.residencialesVisible&&combined.residencialesLayer)
    combined.residencialesLayer.addTo(combined.map).bringToFront();
  if(state.bundle) renderLegend('combined-election-legend','combined-election-note');
  if(fit&&!byId('combined-panel').hidden)
    combined.map.fitBounds(bounds||combined.electionLayer.getBounds(),{padding:[14,14]});
}

function drawLayer(bounds,fit=true) {
  if(state.activeLayer) map.removeLayer(state.activeLayer);
  state.activeLayer=L.geoJSON(state.geoData,{style:featureStyle,onEachFeature}).addTo(map);
  if(state.residencialesVisible&&state.residencialesLayer) state.residencialesLayer.addTo(map).bringToFront();
  const electoralVisible=!byId('electoral-panel').hidden;
  if(fit&&electoralVisible) map.fitBounds(bounds||state.activeLayer.getBounds(),{padding:[14,14]});
  if(combined.map)
    drawCombinedElectoralLayer(bounds,fit&&!byId('combined-panel').hidden);
  refreshLayerStyles();
}

function renderLegend(listId='legendList',noteId='legendNote') {
  const list=byId(listId);
  list.replaceChildren();
  const winners=new Set();
  for(const feature of state.geoData.features) {
    if(featureIsOutsideDrilldown(feature)) continue;
    const winner=winnerFromVotes(partyVotesForFeature(feature)[state.race]);
    if(winner) winners.add(winner[0]);
  }
  [...winners].sort().forEach(party=>{
    const item=document.createElement('li');
    const swatch=document.createElement('span');
    swatch.className='swatch';
    swatch.style.backgroundColor=partyColor(party);
    item.append(swatch,document.createTextNode(partyLabel(party)));
    list.append(item);
  });
  byId(noteId).textContent=state.mode==='margin'
    ?'Color = winning party. Lighter areas have closer races; darker areas have wider margins.'
    :'Color is based on whichever party received the highest vote count in the selected race.';
}

function renderNationwideTotals() {
  byId('nationwideRaceLabel').textContent=raceLabel();
  const totals=state.bundle.nationwide[state.race]||{};
  const ordered=eligibleVotes(totals).sort((a,b)=>b[1]-a[1]);
  const total=ordered.reduce((sum,[,value])=>sum+value,0);
  byId('nationwideTotals').replaceChildren(...ordered.map(([party,value])=>{
    const item=document.createElement('li');
    const name=document.createElement('span');
    name.textContent=partyLabel(party);
    const amount=document.createElement('span');
    amount.className='totals-value';
    amount.textContent=state.mode==='margin'&&total
      ?`${(value/total*100).toFixed(1)}%`:value.toLocaleString();
    item.append(name,amount);
    return item;
  }));
}

function buildRaceButtons() {
  ['raceButtons','combinedRaceButtons'].forEach(holderId=>{
    const holder=byId(holderId);
    holder.replaceChildren(...state.bundle.races.map(race=>{
      const button=document.createElement('button');
      button.type='button';
      button.className=`race-btn${race===state.race?' is-active':''}`;
      button.dataset.race=race;
      button.textContent=race;
      button.addEventListener('click',()=>{
        state.race=race;
        document.querySelectorAll('[data-race]').forEach(item=>{
          item.classList.toggle('is-active',item.dataset.race===race);
        });
        refreshLayerStyles();
      });
      return button;
    }));
  });
}

function refreshMapTitle() {
  byId('mapTitle').textContent=`Mapa Electoral ${state.year}`;
  byId('combined-map-title').textContent=`Mapa Electoral ${state.year}`;
}

function getRaceDataFromDirect(metrics,data) {
  return votesFromDirectData(metrics,data);
}

function getNationwideDirect(metrics,data) {
  const rows=getRaceDataFromDirect(metrics,data);
  const totals={};
  for(const votes of rows.values()) addVotes(totals,votes);
  return totals;
}

function buildLegacyLayerVotes(results,prekeyGeo,geo,scope) {
  if(scope==='precinto') {
    return new Map(geo.features.map(feature=>[
      targetId(feature),legacyVotesByPrekey(results,feature.properties.PREKEY??feature.properties.precinto)
    ]));
  }
  if(scope==='unidad') {
    return new Map(geo.features.map(feature=>[
      targetId(feature),legacyVotesByUnit(
        results,feature.properties.PREKEY??feature.properties.precinto,
        feature.properties.UNIKEY??feature.properties.unidad
      )
    ]));
  }
  const grouped=new Map();
  for(const feature of prekeyGeo.features) {
    const name=normalizedName(feature.properties.municipio);
    if(!grouped.has(name)) grouped.set(name,{});
    addVotes(grouped.get(name),legacyVotesByPrekey(
      results,feature.properties.PREKEY??feature.properties.precinto
    ));
  }
  return grouped;
}

async function loadBundle(year) {
  if(cache.has(`bundle:${year}`)) return cache.get(`bundle:${year}`);
  const promise=(async()=>{
    const shapeYear=year;
    const [municipio,prekeyGeo,catalog]=await Promise.all([
      fetchJSON('data/municipio.geojson'),
      fetchJSON(`data/${shapeYear}/prekey/geometry.geojson`),
      year===2012?Promise.resolve(null):fetchJSON(`data/${year}/prekey/catalog.json`)
    ]);
    if(year===2012) {
      const legacy=await fetchJSON('data/results_2012.json');
      const nationwide={};
      for(const row of Object.values(legacy.by_precinct)) addVotes(nationwide,row);
      const municipalVotes=buildLegacyLayerVotes(legacy,prekeyGeo,null,'municipio');
      return {
        year,municipio,prekeyGeo,legacy,races:legacy.races,nationwide,municipalVotes,
        layerCache:new Map([['municipio',municipalVotes]])
      };
    }
    const metrics=electionMetrics(catalog);
    const prekeyData=await fetchJSON(`data/${year}/prekey/elections_direct.json`);
    const prekeyVotes=getRaceDataFromDirect(metrics,prekeyData);
    const municipalVotes=buildMunicipalVotes(prekeyGeo,prekeyVotes);
    return {
      year,municipio,prekeyGeo,catalog,metrics,
      races:[...metricsByRace(metrics).keys()].sort((a,b)=>RACE_ORDER.indexOf(a)-RACE_ORDER.indexOf(b)),
      prekeyVotes,municipalVotes,nationwide:getNationwideDirect(metrics,prekeyData),
      layerCache:new Map([['precinto',prekeyVotes],['municipio',municipalVotes]])
    };
  })();
  cache.set(`bundle:${year}`,promise);
  try { return await promise; }
  catch(error) { cache.delete(`bundle:${year}`); throw error; }
}

async function loadGeography(scope) {
  if(scope==='municipio') {
    state.geoData=state.bundle.municipio;
    return;
  }
  const prefix=GEO_FILES[scope];
  const shapeYear=state.year;
  const geoPromise=fetchJSON(`data/${shapeYear}/${prefix}/geometry.geojson`);
  if(state.bundle.layerCache.has(scope)) {
    state.geoData=await geoPromise;
    return;
  }
  const [geo,data]=await Promise.all([
    geoPromise,
    state.year===2012?Promise.resolve(null):fetchJSON(`data/${state.year}/${prefix}/elections_direct.json`)
  ]);
  const votes=state.year===2012
    ?buildLegacyLayerVotes(state.bundle.legacy,state.bundle.prekeyGeo,geo,scope)
    :getRaceDataFromDirect(state.bundle.metrics,data);
  state.bundle.layerCache.set(scope,votes);
  state.geoData=geo;
}

async function changeGeography(scope,bounds=null,preserveView=false) {
  state.geo=scope;
  setActiveScope(scope);
  byId('status').textContent='Cargando áreas...';
  try {
    await loadGeography(scope);
    if(scope==='municipio') state.geoData=state.bundle.municipio;
    else state.bundle.geoVotes=state.bundle.layerCache.get(scope);
    drawLayer(bounds,!preserveView);
    byId('status').textContent=`${state.year} · ${scope} · ${state.geoData.features.length} áreas`;
  } catch(error) {
    showError(error.message);
  }
}

async function loadYear(year) {
  const request=++state.request;
  state.loading=true;
  document.querySelectorAll('[data-year],[data-race]').forEach(button=>button.disabled=true);
  clearError();
  byId('status').textContent=`Cargando elecciones de ${year}...`;
  try {
    const bundle=await loadBundle(year);
    if(request!==state.request) return;
    state.year=year;
    state.bundle=bundle;
    state.selectedMunicipio=null;
    state.selectedPrecinct=null;
    if(!bundle.races.includes(state.race)) state.race=bundle.races[0];
    document.querySelectorAll('[data-year]').forEach(button=>{
      button.classList.toggle('is-active',Number(button.dataset.year)===year);
    });
    state.geo='municipio';
    setActiveScope('municipio');
    buildRaceButtons();
    refreshMapTitle();
    await loadGeography('municipio');
    if(request!==state.request) return;
    drawLayer(null,true);
    byId('status').textContent=`${year} · municipio · ${state.geoData.features.length} áreas`;
  } catch(error) {
    if(request===state.request) showError(error.message);
  } finally {
    if(request===state.request) {
      state.loading=false;
      document.querySelectorAll('[data-year],[data-race]').forEach(button=>button.disabled=false);
    }
  }
}

function makeResidencialesLayer(data,pane) {
  const options={
    interactive:false,
    style:{weight:1.1,color:'#6e2b1d',fillOpacity:.32,fillColor:'#e39c72'}
  };
  if(pane) options.pane=pane;
  return L.geoJSON(data,options);
}

async function ensureResidencialesLayers() {
  const data=await fetchJSON('data/residenciales_2009.geojson');
  if(!state.residencialesLayer) state.residencialesLayer=makeResidencialesLayer(data,'residencialesPane');
  if(combined.map&&!combined.residencialesLayer)
    combined.residencialesLayer=makeResidencialesLayer(data,'combinedResidencialesPane');
}

function wireControls() {
  document.querySelectorAll('[data-combined-census-year]').forEach(button=>{
    button.addEventListener('click',()=>{
      combined.censusYear=Number(button.dataset.combinedCensusYear);
      document.querySelectorAll('[data-combined-census-year]').forEach(item=>{
        const active=item===button;
        item.classList.toggle('is-active',active);
        item.setAttribute('aria-pressed',String(active));
      });
      loadCombinedCensusData();
    });
  });
  byId('combined-category').addEventListener('change',()=>loadCombinedCensusData());
  byId('combined-metric').addEventListener('change',()=>loadCombinedCensusData());
  byId('combined-transparency').addEventListener('input',event=>{
    combined.transparency=Number(event.currentTarget.value);
    byId('combined-transparency-value').textContent=`${combined.transparency}%`;
    if(combined.censusLayer) combined.censusLayer.setStyle(combinedCensusStyle);
  });
  document.querySelectorAll('[data-block-group-year]').forEach(button=>{
    button.addEventListener('click',()=>{
      blockGroup.year=Number(button.dataset.blockGroupYear);
      document.querySelectorAll('[data-block-group-year]').forEach(item=>{
        const active=item===button;
        item.classList.toggle('is-active',active);
        item.setAttribute('aria-pressed',String(active));
      });
      loadBlockGroupData();
    });
  });
  byId('block-group-metric').addEventListener('change',()=>loadBlockGroupData());
  document.querySelectorAll('[data-year]').forEach(button=>{
    button.addEventListener('click',()=>loadYear(Number(button.dataset.year)));
  });
  document.querySelectorAll('[data-mode]').forEach(button=>{
    button.addEventListener('click',()=>{
      state.mode=button.dataset.mode;
      document.querySelectorAll('[data-mode]').forEach(item=>item.classList.toggle('is-active',item===button));
      refreshLayerStyles();
    });
  });
  document.querySelectorAll('[data-scope]').forEach(button=>{
    button.addEventListener('click',()=>{
      const next=button.dataset.scope;
      if(next==='municipio') {
        state.selectedMunicipio=null;
        state.selectedPrecinct=null;
      } else if(next==='precinto') {
        state.selectedPrecinct=null;
      }
      changeGeography(next,null,next==='precinto'||(next==='unidad'&&state.geo==='precinto'));
    });
  });
  document.querySelectorAll('[data-residenciales-toggle]').forEach(button=>{
    button.addEventListener('click',async()=>{
      try {
        await ensureResidencialesLayers();
        state.residencialesVisible=!state.residencialesVisible;
        if(state.residencialesVisible) {
          state.residencialesLayer.addTo(map).bringToFront();
          if(combined.map) combined.residencialesLayer.addTo(combined.map).bringToFront();
        } else {
          map.removeLayer(state.residencialesLayer);
          if(combined.residencialesLayer) combined.map.removeLayer(combined.residencialesLayer);
        }
        document.querySelectorAll('[data-residenciales-toggle]').forEach(item=>{
          item.classList.toggle('is-active',state.residencialesVisible);
          item.setAttribute('aria-pressed',String(state.residencialesVisible));
        });
      } catch(error) {
        showError(error.message);
      }
    });
  });
  const tabs=['electoral','block-group','combined'];
  for(const name of tabs) {
    byId(`${name}-tab`).addEventListener('click',()=>selectTab(name));
    byId(`${name}-tab`).addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const current=tabs.indexOf(name);
      const nextIndex=event.key==='Home'?0:event.key==='End'?tabs.length-1:
        (current+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      const next=tabs[nextIndex];
      selectTab(next);
      byId(`${next}-tab`).focus();
    });
  }
}

function formatCensusValue(metric,value) {
  if(!numeric(value)) return 'No estimate';
  if(metric.kind.endsWith('percentage')||metric.id.includes('_pct'))
    return `${value.toLocaleString(undefined,{maximumFractionDigits:1})}%`;
  if(/income|earnings/i.test(metric.label))
    return `$${Math.round(value).toLocaleString()}`;
  return value.toLocaleString(undefined,{
    maximumFractionDigits:metric.kind==='intensive'?1:0
  });
}

function censusBreaks(values) {
  const sorted=values.filter(numeric).sort((a,b)=>a-b);
  if(!sorted.length) return [];
  return [0.2,0.4,0.6,0.8].map(q=>
    sorted[Math.min(sorted.length-1,Math.ceil(q*sorted.length)-1)]
  );
}

function renderCensusLegend(metric,breaks,titleId,listId) {
  byId(titleId).textContent=metric.label;
  const list=byId(listId);
  list.replaceChildren();
  for(let i=0;breaks.length&&i<CENSUS_COLORS.length;i++) {
    const item=document.createElement('li');
    const swatch=document.createElement('span');
    swatch.className='swatch';
    swatch.style.backgroundColor=CENSUS_COLORS[i];
    const label=document.createElement('span');
    const low=i===0?'':formatCensusValue(metric,breaks[i-1]);
    const high=i===CENSUS_COLORS.length-1?'':formatCensusValue(metric,breaks[i]);
    label.textContent=i===0?`≤ ${high}`:i===CENSUS_COLORS.length-1?`> ${low}`:`${low} – ${high}`;
    item.append(swatch,label);
    list.append(item);
  }
  const missing=document.createElement('li');
  const missingSwatch=document.createElement('span');
  missingSwatch.className='swatch census-no-data';
  const missingLabel=document.createElement('span');
  missingLabel.textContent='No estimate';
  missing.append(missingSwatch,missingLabel);
  list.append(missing);
}

function censusPreferredMetric(topic,metrics) {
  const preferred=CENSUS_DEFAULT_METRICS[topic];
  return metrics.find(metric=>metric.id===preferred)?.id||metrics[0]?.id||'';
}

function updateMetricOptions(metrics,category,metricSelect) {
  const oldMetric=metricSelect.value;
  const availableTopics=new Set(metrics.map(metric=>metric.topic));
  const topics=Object.keys(CENSUS_TOPICS).filter(topic=>availableTopics.has(topic));
  const oldTopic=category.value;
  category.replaceChildren();
  topics.forEach(topic=>{
    const option=document.createElement('option');
    option.value=topic;
    option.textContent=CENSUS_TOPICS[topic];
    category.append(option);
  });
  const selectedTopic=topics.includes(oldTopic)?oldTopic:
    topics.includes('poverty')?'poverty':topics[0];
  category.value=selectedTopic||'';
  const matches=metrics.filter(metric=>metric.topic===category.value)
    .sort((a,b)=>a.label.localeCompare(b.label));
  metricSelect.replaceChildren();
  matches.forEach(metric=>{
    const option=document.createElement('option');
    option.value=metric.id;
    option.textContent=metric.label;
    metricSelect.append(option);
  });
  metricSelect.value=matches.some(metric=>metric.id===oldMetric)?oldMetric:
    censusPreferredMetric(category.value,matches);
  return matches.find(metric=>metric.id===metricSelect.value);
}

function updateBlockGroupMetricOptions(metrics) {
  const categoryButtons=byId('block-group-category-buttons');
  const metricSelect=byId('block-group-metric');
  const oldMetric=metricSelect.value;
  const availableTopics=new Set(metrics.map(metric=>metric.topic));
  const topics=Object.keys(CENSUS_TOPICS).filter(topic=>availableTopics.has(topic));
  if(!topics.length) return null;
  blockGroup.category=topics.includes(blockGroup.category)?blockGroup.category:
    topics.includes('poverty')?'poverty':topics[0];
  categoryButtons.replaceChildren(...topics.map(topic=>{
    const button=document.createElement('button');
    button.type='button';
    button.className=`race-btn${topic===blockGroup.category?' is-active':''}`;
    button.dataset.topic=topic;
    button.textContent=CENSUS_TOPICS[topic];
    button.setAttribute('aria-pressed',String(topic===blockGroup.category));
    button.addEventListener('click',()=>{
      if(blockGroup.category===topic) return;
      blockGroup.category=topic;
      categoryButtons.querySelectorAll('.race-btn').forEach(item=>{
        const active=item===button;
        item.classList.toggle('is-active',active);
        item.setAttribute('aria-pressed',String(active));
      });
      loadBlockGroupData();
    });
    return button;
  }));
  const matches=metrics.filter(metric=>metric.topic===blockGroup.category)
    .sort((a,b)=>a.label.localeCompare(b.label));
  metricSelect.replaceChildren(...matches.map(metric=>{
    const option=document.createElement('option');
    option.value=metric.id;
    option.textContent=metric.label;
    return option;
  }));
  metricSelect.value=matches.some(metric=>metric.id===oldMetric)?oldMetric:
    censusPreferredMetric(blockGroup.category,matches);
  return matches.find(metric=>metric.id===metricSelect.value);
}

function combinedCensusStyle(feature) {
  const value=feature.properties.combinedCensusValue;
  const index=combined.breaks.findIndex(boundary=>value!==null&&value<=boundary);
  const opacity=1-combined.transparency/100;
  return {
    color:'#fff',weight:.45,opacity,
    fillColor:value===null?'#c8c8c8':CENSUS_COLORS[index<0?CENSUS_COLORS.length-1:index],
    fillOpacity:opacity
  };
}

async function loadCombinedCensusData() {
  const request=++combined.censusRequest;
  const year=combined.censusYear;
  const root=`data/census/acs/${year}`;
  const catalogPath=`${root}/catalog.json`;
  const geometryPath=`data/census/tiger_${year}_72_bg.geojson`;
  let metricPath='';
  byId('combined-error').hidden=true;
  byId('combined-status').hidden=false;
  byId('combined-status').textContent='Loading direct ACS estimates...';
  byId('combined-map-status').textContent='Loading direct ACS estimates...';
  try {
    const [catalog,geometry]=await Promise.all([
      combined.censusCatalogYear===year?
        Promise.resolve(combined.censusCatalog):fetchJSON(catalogPath),
      fetchJSON(geometryPath)
    ]);
    if(request!==combined.censusRequest) return;
    if(!Array.isArray(catalog.metrics))
      throw new Error(`Invalid block-group metric catalog: ${catalogPath}`);
    combined.censusCatalog=catalog;
    combined.censusCatalogYear=year;
    const metric=updateMetricOptions(
      catalog.metrics,byId('combined-category'),byId('combined-metric')
    );
    if(!metric) throw new Error(`No block-group indicators are available for ${year}.`);
    const field=metric.fields.direct;
    metricPath=`${root}/${metric.table}_direct.json`;
    const dataset=await fetchJSON(metricPath);
    if(request!==combined.censusRequest) return;
    const values=dataset.values?.[field];
    if(!Array.isArray(dataset.ids)||!Array.isArray(values))
      throw new Error(`Metric field ${field} is missing from ${metricPath}.`);
    const indexById=new Map(dataset.ids.map((id,index)=>[String(id),index]));
    const observed=[];
    geometry.features.forEach(feature=>{
      feature.properties=feature.properties||{};
      const index=indexById.get(String(feature.properties.GEOID??''));
      const value=index===undefined?null:values[index];
      feature.properties.combinedCensusValue=numeric(value)?value:null;
      if(numeric(value)) observed.push(value);
    });
    combined.breaks=censusBreaks(observed);
    const layer=L.geoJSON(geometry,{
      pane:'combinedCensusPane',
      interactive:false,
      style:combinedCensusStyle,
    });
    if(combined.censusLayer) combined.map.removeLayer(combined.censusLayer);
    combined.censusLayer=layer.addTo(combined.map);
    const period=`${catalog.acs_period_start}–${catalog.acs_period_end}`;
    byId('combined-census-title').textContent=`${metric.label} · TIGER/Line ${year}`;
    byId('combined-census-period').textContent=`ACS ${period}`;
    byId('combined-census-description').textContent=
      `${metric.description} Universe: ${metric.universe}. Direct block-group estimate.`;
    renderCensusLegend(
      metric,combined.breaks,'combined-census-title','combined-census-legend'
    );
    byId('combined-census-title').textContent=`${metric.label} · TIGER/Line ${year}`;
    const status=
      `${geometry.features.length.toLocaleString()} block groups · `+
      `${observed.length.toLocaleString()} with estimates · `+
      `${Math.max(0,geometry.features.length-observed.length).toLocaleString()} without estimates`;
    byId('combined-status').textContent=status;
    byId('combined-map-status').textContent=status;
  } catch(error) {
    cache.delete(catalogPath);
    cache.delete(geometryPath);
    if(metricPath) cache.delete(metricPath);
    if(request!==combined.censusRequest) return;
    combined.censusCatalogYear=null;
    byId('combined-error').textContent=error.message;
    byId('combined-error').hidden=false;
    byId('combined-status').hidden=true;
    byId('combined-map-status').textContent=error.message;
  }
}

async function loadBlockGroupData() {
  const request=++blockGroup.request;
  const year=blockGroup.year;
  const root=`data/census/acs/${year}`;
  const catalogPath=`${root}/catalog.json`;
  const geometryPath=`data/census/tiger_${year}_72_bg.geojson`;
  let metricPath='';
  byId('block-group-error').hidden=true;
  blockGroup.statusMessage='Loading block-group estimates...';
  byId('block-group-status').hidden=false;
  byId('block-group-status').textContent=blockGroup.statusMessage;
  try {
    const [catalog,geometry]=await Promise.all([
      blockGroup.catalogKey===year?Promise.resolve(blockGroup.catalog):fetchJSON(catalogPath),
      fetchJSON(geometryPath)
    ]);
    if(request!==blockGroup.request) return;
    if(!Array.isArray(catalog.metrics))
      throw new Error(`Invalid block-group metric catalog: ${catalogPath}`);
    blockGroup.catalog=catalog;
    blockGroup.catalogKey=year;
    const metric=updateBlockGroupMetricOptions(catalog.metrics);
    if(!metric) throw new Error(`No block-group indicators are available for ${year}.`);
    const field=metric.fields.direct;
    metricPath=`${root}/${metric.table}_direct.json`;
    const dataset=await fetchJSON(metricPath);
    if(request!==blockGroup.request) return;
    const values=dataset.values?.[field];
    if(!Array.isArray(dataset.ids)||!Array.isArray(values))
      throw new Error(`Metric field ${field} is missing from ${metricPath}.`);
    const indexById=new Map(dataset.ids.map((id,index)=>[String(id),index]));
    const observed=[];
    geometry.features.forEach(feature=>{
      const geoid=String(feature.properties.GEOID??'');
      const index=indexById.get(geoid);
      const value=index===undefined?null:values[index];
      feature.properties.censusValue=numeric(value)?value:null;
      if(numeric(value)) observed.push(value);
    });
    const breaks=censusBreaks(observed);
    const dataLayer=L.geoJSON(geometry,{
      style:feature=>{
        const value=feature.properties.censusValue;
        const index=breaks.findIndex(boundary=>value!==null&&value<=boundary);
        return {
          color:'#fff',weight:.45,
          fillColor:value===null?'#c8c8c8':CENSUS_COLORS[index<0?CENSUS_COLORS.length-1:index],
          fillOpacity:value===null ? .75 : .86
        };
      },
      onEachFeature:(feature,layer)=>{
        const details=document.createElement('span');
        details.textContent=
          `Block group ${feature.properties.GEOID} · `+
          formatCensusValue(metric,feature.properties.censusValue);
        layer.bindTooltip(details,{sticky:true,opacity:.96,direction:'top'});
      }
    });
    if(blockGroup.dataLayer) blockGroup.map.removeLayer(blockGroup.dataLayer);
    blockGroup.dataLayer=dataLayer.addTo(blockGroup.map);
    if(blockGroup.geometryYear!==year) {
      blockGroup.map.fitBounds(dataLayer.getBounds(),{padding:[14,14]});
      blockGroup.geometryYear=year;
    }
    const period=`${catalog.acs_period_start}–${catalog.acs_period_end}`;
    byId('block-group-vintage-label').textContent=`ACS ${period} · TIGER/Line ${year}`;
    byId('block-group-map-title').textContent=`${metric.label} · TIGER/Line ${year}`;
    byId('block-group-description').textContent=
      `${metric.description} Universe: ${metric.universe}. `+
      'Direct ACS estimate at block-group geography; not allocated to electoral areas.';
    renderCensusLegend(metric,breaks,'block-group-legend-title','block-group-legend-list');
    const missing=Math.max(0,geometry.features.length-observed.length);
    blockGroup.statusMessage=
      `${geometry.features.length.toLocaleString()} TIGER/Line block groups · `+
      `${observed.length.toLocaleString()} with estimates · ${missing.toLocaleString()} without estimates`;
    byId('block-group-status').textContent=blockGroup.statusMessage;
  } catch(error) {
    cache.delete(catalogPath);
    cache.delete(geometryPath);
    if(metricPath) cache.delete(metricPath);
    if(request!==blockGroup.request) return;
    byId('block-group-error').textContent=error.message;
    byId('block-group-error').hidden=false;
    blockGroup.statusMessage='';
    byId('block-group-status').hidden=true;
  }
}

async function showBlockGroupMap() {
  if(!blockGroup.map) {
    blockGroup.map=L.map('block-group-map',{preferCanvas:true,attributionControl:false})
      .setView([18.2,-66.4],9);
  }
  blockGroup.map.invalidateSize({pan:false});
  if(blockGroup.catalogKey!==blockGroup.year||!blockGroup.dataLayer)
    await loadBlockGroupData();
}

async function showCombinedMap() {
  if(!state.bundle) await loadYear(state.year);
  if(!combined.map) {
    combined.map=L.map('combined-map',{preferCanvas:true,attributionControl:false})
      .setView(map.getCenter(),map.getZoom());
    combined.map.createPane('combinedCensusPane');
    combined.map.getPane('combinedCensusPane').style.zIndex='450';
    combined.map.createPane('combinedResidencialesPane');
    combined.map.getPane('combinedResidencialesPane').style.zIndex='640';
  }
  combined.map.invalidateSize({pan:false});
  if(!combined.electionLayer) drawCombinedElectoralLayer(null,true);
  if(state.residencialesVisible) {
    await ensureResidencialesLayers();
    combined.residencialesLayer.addTo(combined.map).bringToFront();
  }
  if(combined.censusCatalogYear!==combined.censusYear||!combined.censusLayer)
    await loadCombinedCensusData();
}

function selectTab(name) {
  const tabs=['electoral','block-group','combined'];
  if(activeTab==='combined'&&name!=='combined'&&combined.map)
    map.setView(combined.map.getCenter(),combined.map.getZoom(),{animate:false});
  else if(name==='combined'&&activeTab!=='combined'&&combined.map)
    combined.map.setView(map.getCenter(),map.getZoom(),{animate:false});
  for(const tab of tabs) {
    const active=tab===name;
    byId(`${tab}-tab`).classList.toggle('is-active',active);
    byId(`${tab}-tab`).setAttribute('aria-selected',String(active));
    byId(`${tab}-tab`).tabIndex=active?0:-1;
    byId(`${tab}-panel`).hidden=!active;
  }
  byId('electoral-sidebar').hidden=name!=='electoral'&&name!=='combined';
  byId('block-group-sidebar').hidden=name!=='block-group';
  byId('combined-controls').hidden=name!=='combined';
  activeTab=name;
  if(name==='electoral') requestAnimationFrame(()=>map.invalidateSize({pan:false}));
  else if(name==='block-group') requestAnimationFrame(()=>showBlockGroupMap());
  else requestAnimationFrame(()=>showCombinedMap());
}

wireControls();
selectTab('electoral');
loadYear(2024);
