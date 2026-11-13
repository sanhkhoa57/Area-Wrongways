"""Stateless Wrongway search: bitboard BFS, alpha-beta and tactical wall ordering."""
import time
from collections import deque

TIME_LIMIT = 0.35
INF = 999
WIN = 100000
WALL_WEIGHT = 2.2
WALL_POWER = 0.8
TEMPO = 0.5
INNER_LIMIT = 6
ROOT_LIMIT = 20
ROOT_RISK = 0.0


def _geometry():
    bits = tuple(1 << p for p in range(81))
    rows = tuple(511 << (9 * r) for r in range(9))
    whole = (1 << 81) - 1
    left = sum(bits[r * 9] for r in range(9))
    right = left << 8
    initial = (whole ^ rows[0], whole ^ right, whole ^ rows[8], whole ^ left)
    walls = []
    for w in range(128):
        r, c = divmod(w % 64, 8)
        if w < 64:
            down = bits[r * 9 + c] | bits[r * 9 + c + 1]
            remove = (down << 9, 0, down, 0)
            conflict = (sum(1 << (r * 8 + k) for k in range(max(0,c-1), min(8,c+2))), 1 << (r*8+c))
        else:
            across = bits[r * 9 + c] | bits[(r+1) * 9 + c]
            remove = (0, across, 0, across << 1)
            conflict = (1 << (r*8+c), sum(1 << (k*8+c) for k in range(max(0,r-1), min(8,r+2))))
        walls.append((remove, conflict, r, c))
    return bits, rows, initial, tuple(walls)


BITS, ROWS, INITIAL, WALLS = _geometry()
DIRECTIONS = (-9, 1, 9, -1)
del _geometry


class SearchTimeout(Exception):
    pass


class Field:
    __slots__ = ('layers', 'seen', 'can', 'deadline', 'known')

    def __init__(self, goal, can, deadline, distances=None):
        self.can = can
        self.deadline = deadline
        self.layers = [ROWS[goal]]
        self.seen = ROWS[goal]
        self.known = {}
        if distances is not None:
            self.known = {r*9+c: d if d >= 0 else INF
                          for r, row in enumerate(distances) for c, d in enumerate(row)}
            layers = [0] * (max(d for d in self.known.values() if d < INF) + 1)
            for p, d in self.known.items():
                if d < INF:
                    layers[d] |= BITS[p]
            self.layers = layers
            self.seen = sum(layers)

    def dist(self, p):
        cached = self.known.get(p)
        if cached is not None:
            return cached
        bit = BITS[p]
        for d, layer in enumerate(self.layers):
            if layer & bit:
                self.known[p] = d
                return d
        u, r, d, l = self.can
        f = self.layers[-1]
        seen = self.seen
        while f:
            if time.perf_counter() > self.deadline:
                raise SearchTimeout
            f = ((f << 9 & u) | (f >> 1 & r) | (f >> 9 & d) | (f << 1 & l)) & ~seen
            if not f:
                break
            seen |= f
            self.layers.append(f)
            self.seen = seen
            if f & bit:
                value = len(self.layers) - 1
                self.known[p] = value
                return value
        self.known[p] = INF
        return INF

    def paths(self, p):
        distance = self.dist(p)
        if distance >= INF:
            return (0, 0, 0, 0)
        frontier = BITS[p]
        edges = [0, 0, 0, 0]
        for depth in range(distance, 0, -1):
            if time.perf_counter() > self.deadline:
                raise SearchTimeout
            near = self.layers[depth-1]
            u = frontier & self.can[0] & (near << 9)
            r = frontier & self.can[1] & (near >> 1)
            d = frontier & self.can[2] & (near >> 9)
            l = frontier & self.can[3] & (near << 1)
            edges[0] |= u
            edges[1] |= r
            edges[2] |= d
            edges[3] |= l
            frontier = (u >> 9) | (r << 1) | (d << 9) | (l >> 1)
        return tuple(edges)


def moves(p, q, can):
    result = []
    bit = BITS[p]
    for direction, delta in enumerate(DIRECTIONS):
        if not can[direction] & bit:
            continue
        target = p + delta
        if target != q:
            result.append(target)
        elif can[direction] & BITS[q]:
            result.append(q + delta)
        else:
            for side in ((direction+1) % 4, (direction+3) % 4):
                if can[side] & BITS[q]:
                    result.append(q + DIRECTIONS[side])
    return result


class Solver:
    def __init__(self, obs, deadline):
        self.deadline = deadline
        self.fields = {}
        self.tt = {}
        self.path = set()
        self.nodes = 0
        self.depth_reached = 0
        self.last_score = None
        self.goal = obs['you']['goal_row']
        self.other_goal = obs['opponent']['goal_row']
        self.root_distances = obs['distances']
        self.root_wall_key = None
        self.wall_value = WALL_WEIGHT
        self.turns_left = obs.get('max_turns', 500) - obs.get('turn', 0)
        fraction = obs.get('turn',0)/max(1,obs.get('max_turns',500))
        self.progress = 0.015 + 0.5*max(0,(fraction-0.2)/0.8)

    def tick(self):
        if time.perf_counter() > self.deadline:
            raise SearchTimeout

    def endgame(self, p, q, g, og, can, legal, own_walls):
        """Retrograde proof with fixed walls; a proof of win needs no more walls."""
        limit = min(self.deadline, time.perf_counter()+0.10)
        span = 81*81
        graph = [tuple(moves(i, -1, can)) for i in range(81)]
        contact = {(i,j): tuple(moves(i,j,can)) for i in range(81) for j in graph[i]}
        previous = [[] for _ in range(2*span)]
        outcome = [0]*(2*span)
        distance = [0]*(2*span)
        remaining = [0]*(2*span)
        longest = [0]*(2*span)
        queue = deque()
        for a in range(81):
            if time.perf_counter() > limit:
                return None
            for b in range(81):
                if a == b:
                    continue
                pair = a*81+b
                if a//9 == g or b//9 == og:
                    outcome[pair] = 1 if a//9 == g else -1
                    outcome[span+pair] = -outcome[pair]
                    queue.extend((pair,span+pair))
                    continue
                pm = contact.get((a,b),graph[a])
                qm = contact.get((b,a),graph[b])
                remaining[pair] = len(pm)+1
                remaining[span+pair] = len(qm)+1
                for m in pm:
                    previous[span+m*81+b].append(pair)
                previous[span+pair].append(pair)
                for m in qm:
                    previous[a*81+m].append(span+pair)
                previous[pair].append(span+pair)
        count = 0
        while queue:
            index = queue.popleft()
            count += 1
            if count % 128 == 0 and time.perf_counter() > limit:
                return None
            value, dt = outcome[index],distance[index]
            for parent in previous[index]:
                if outcome[parent]:
                    continue
                if value == -1:
                    outcome[parent] = 1
                    distance[parent] = dt+1
                    queue.append(parent)
                else:
                    remaining[parent] -= 1
                    longest[parent] = max(longest[parent],dt)
                    if remaining[parent] == 0:
                        outcome[parent] = -1
                        distance[parent] = longest[parent]+1
                        queue.append(parent)
        options = []
        for m in legal+[p]:
            index = span+m*81+q
            value, dt = -outcome[index],distance[index]+1
            if dt > self.turns_left:
                value = 0
            if own_walls > 0 and value != 1:
                continue
            pace = -dt if value==1 else dt if value==-1 else 0
            options.append(((value,pace,m!=p),m))
        if not options:
            return None
        m = max(options)[1]
        return {'type':'pass'} if m==p else action(m)

    def field(self, h, v, goal, can):
        key = h, v, goal
        f = self.fields.get(key)
        if f is None:
            self.tick()
            if len(self.fields) >= 4000:
                self.fields.clear()
            distances = self.root_distances if (h, v) == self.root_wall_key and goal == self.goal else None
            f = Field(goal, can, self.deadline, distances)
            self.fields[key] = f
        return f

    def evaluate(self, p, q, g, og, c, oc, f, of, can):
        dp, dq = f.dist(p), of.dist(q)
        # Real next-step distance accounts for jumps and forced diagonals.
        if BITS[p] & ((BITS[q] << 9) | (BITS[q] >> 9) | (BITS[q] << 1) | (BITS[q] >> 1)):
            pm = moves(p, q, can)
            qm = moves(q, p, can)
            if pm:
                dp = min(f.dist(m) for m in pm) + 1
            if qm:
                dq = min(of.dist(m) for m in qm) + 1
        resource = self.wall_value * (c ** WALL_POWER - oc ** WALL_POWER)
        # Prefer progress among otherwise equal states; this also breaks local cycles.
        center = 0.015 * (abs(q % 9 - 4) - abs(p % 9 - 4))
        flexibility = 0
        if c or oc:
            for i, delta in enumerate(DIRECTIONS):
                if oc and can[i] & BITS[p] and f.dist(p+delta) == dp-1:
                    flexibility += 1
                if c and can[i] & BITS[q] and of.dist(q+delta) == dq-1:
                    flexibility -= 1
        return dq - dp + resource + TEMPO + center + 0.08*flexibility

    def walls(self, p, q, g, og, c, oc, h, v, can, f, of, limit, legal=None):
        if c <= 0:
            return []
        self.tick()
        paths = of.paths(q)
        dp, dq = f.dist(p), of.dist(q)
        pool = []
        for w, (remove, conflict, r, col) in enumerate(WALLS):
            self.tick()
            if legal is not None and w not in legal:
                continue
            if h & conflict[0] or v & conflict[1]:
                continue
            offensive = (paths[0]&remove[0] or paths[1]&remove[1]
                         or paths[2]&remove[2] or paths[3]&remove[3])
            if not offensive and legal is None:
                continue
            nc = tuple(can[i] & ~remove[i] for i in range(4))
            nh, nv = (h | (1 << w), v) if w < 64 else (h, v | (1 << (w-64)))
            nf, nof = self.field(nh, nv, g, nc), self.field(nh, nv, og, nc)
            ndq = nof.dist(q)
            if ndq >= INF:
                continue
            ndp = nf.dist(p)
            if ndp >= INF:
                continue
            gain = (ndq-dq) - (ndp-dp)
            # Keep zero-gain setup walls at the root and urgent goal blocks.
            if gain < 0 and dq > 2 and legal is None and not (ndq-dq >= 3 and c >= 4):
                continue
            if legal is None and gain == 0 and dq > 2 and not (dp < dq and c >= oc):
                continue
            proximity = abs(r+0.5-q//9) + abs(col+0.5-q%9)
            score = ndq - ndp - 1 - 0.02 * proximity
            pool.append((score, 81+w, (p, q, g, og, c-1, oc, nh, nv, nc), nf, nof))
        pool.sort(key=lambda item: (-item[0], item[1]))
        return pool[:limit]

    def search(self, state, depth, alpha, beta, ply):
        self.tick()
        self.nodes += 1
        p, q, g, og, c, oc, h, v, can = state
        if q // 9 == og:
            return -WIN + ply
        if ply >= self.turns_left:
            return 0
        pm = moves(p, q, can)
        for m in pm:
            if m // 9 == g:
                return WIN - ply - 1
        f, of = self.field(h, v, g, can), self.field(h, v, og, can)
        if depth <= 0:
            return self.evaluate(p, q, g, og, c, oc, f, of, can)
        key = p, q, g, c, oc, h, v
        if key in self.path:
            return self.evaluate(p,q,g,og,c,oc,f,of,can)
        entry = self.tt.get(key)
        preferred = entry[3] if entry else None
        start_alpha, start_beta = alpha, beta
        if entry and entry[0] >= depth:
            _, flag, value, _ = entry
            if abs(value) > WIN-1000:
                value = value-ply if value > 0 else value+ply
            if flag == 0:
                return value
            if flag == 1:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if alpha >= beta:
                return value
        pm.sort(key=lambda m: (m != preferred, f.dist(m), abs(m%9-4)))
        self.path.add(key)
        best, action = -2*WIN, None
        try:
            if preferred is not None and preferred >= 81 and c > 0:
                w = preferred-81
                remove, conflict, _, _ = WALLS[w]
                if not (h&conflict[0] or v&conflict[1]):
                    nc = tuple(can[i]&~remove[i] for i in range(4))
                    nh,nv = (h|(1<<w),v) if w<64 else (h,v|(1<<(w-64)))
                    nf,nof = self.field(nh,nv,g,nc),self.field(nh,nv,og,nc)
                    if nf.dist(p)<INF and nof.dist(q)<INF:
                        best = self.child_value((q,p,og,g,oc,c-1,nh,nv,nc),depth-1,alpha,beta,ply+1,True)
                        action = preferred
                        alpha = max(alpha,best)
            for m in pm if alpha < beta else ():
                value = self.child_value((q,m,og,g,oc,c,h,v,can),depth-1,alpha,beta,ply+1,action is None)
                if value > best:
                    best, action = value, m
                alpha = max(alpha, best)
                if alpha >= beta:
                    break
            if alpha < beta and c > 0:
                candidates = self.walls(p,q,g,og,c,oc,h,v,can,f,of,INNER_LIMIT if depth>1 else min(6,INNER_LIMIT))
                if preferred is not None:
                    candidates.sort(key=lambda item: item[1] != preferred)
                for _, code, child, _, _ in candidates:
                    if code == action and code == preferred:
                        continue
                    cp,cq,cg,cog,cc,coc,ch,cv,cn = child
                    value = self.child_value((cq,cp,cog,cg,coc,cc,ch,cv,cn),depth-1,alpha,beta,ply+1,action is None)
                    if value > best:
                        best, action = value, code
                    alpha = max(alpha, best)
                    if alpha >= beta:
                        break
        finally:
            self.path.remove(key)
        stored = best+ply if best > WIN-1000 else best-ply if best < -WIN+1000 else best
        flag = 2 if best <= start_alpha else 1 if best >= start_beta else 0
        if len(self.tt) >= 12000:
            self.tt.clear()
        self.tt[key] = depth, flag, stored, action
        return best

    def child_value(self, state, depth, alpha, beta, ply, first):
        if first or depth <= 1:
            return -self.search(state,depth,-beta,-alpha,ply)
        value = -self.search(state,depth,-alpha-0.000001,-alpha,ply)
        if alpha < value < beta:
            value = -self.search(state,depth,-beta,-alpha,ply)
        return value

    def choose(self, obs, fallback):
        p = obs['you']['position'][0]*9+obs['you']['position'][1]
        q = obs['opponent']['position'][0]*9+obs['opponent']['position'][1]
        h = sum(1 << (r*8+c) for r,c in obs['walls']['horizontal'])
        v = sum(1 << (r*8+c) for r,c in obs['walls']['vertical'])
        can = INITIAL
        for w, (remove, _, _, _) in enumerate(WALLS):
            if (h >> w & 1) if w < 64 else (v >> (w-64) & 1):
                can = tuple(can[i] & ~remove[i] for i in range(4))
        self.root_wall_key = h, v
        g, og = self.goal, self.other_goal
        c, oc = obs['you']['walls_remaining'], obs['opponent']['walls_remaining']
        best = fallback
        if oc == 0:
            legal_cells = [r*9+col for r,col in obs['legal_moves']]
            exact = self.endgame(p,q,g,og,can,legal_cells,c)
            if exact is not None:
                return exact
        f, of = self.field(h,v,g,can), self.field(h,v,og,can)
        candidates = []
        move_risk = {}
        for r,col in obs['legal_moves']:
            m = r*9+col
            candidates.append((of.dist(q)-f.dist(m),m,(m,q,g,og,c,oc,h,v,can),f,of))
        if c > 0:
            legal = {(0 if w['orientation']=='H' else 64)+w['row']*8+w['col'] for w in obs['legal_walls']}
            all_walls = self.walls(p,q,g,og,c,oc,h,v,can,f,of,128,legal)
            dp, dq = f.dist(p), of.dist(q)
            if ROOT_RISK and oc > 0:
                baseline_risk = max((item[3].dist(p)-dp-item[4].dist(q)+dq
                                     for item in all_walls),default=0)
                for _, code, _, _, _ in candidates:
                    risk = max((item[3].dist(code)-f.dist(code)-item[4].dist(q)+dq
                                for item in all_walls if item[3].dist(code)<INF),default=0)
                    move_risk[code] = ROOT_RISK*(risk-baseline_risk)
            threats = sorted(all_walls,
                             key=lambda item: (item[3].dist(p)-dp)-(item[4].dist(q)-dq),
                             reverse=True) if oc > 0 else []
            attacks = [item for item in all_walls
                       if (item[4].dist(q)-dq)-(item[3].dist(p)-dp) > 0
                       or (dq <= 2 and item[4].dist(q)>dq)]
            selected = attacks[:ROOT_LIMIT]
            # A wall can neutralize a future trap by conflicting with it,
            # even when it cuts neither pawn's current shortest route.
            for threat in threats[:3]:
                if threat[3].dist(p)-dp < 3:
                    continue
                if (threat[3].dist(p)-dp)-(threat[4].dist(q)-dq) < 2:
                    continue
                tw = threat[1]-81
                conflict = WALLS[tw][1]
                defensive = [item for item in all_walls
                             if item[1]-81 < 64 and conflict[0] >> (item[1]-81) & 1]
                defensive += [item for item in all_walls
                              if item[1]-81 >= 64 and conflict[1] >> (item[1]-145) & 1]
                for item in defensive:
                    if item not in selected:
                        selected.append(item)
            candidates += selected
        candidates.sort(key=lambda item: (-item[0],item[1]))
        if not candidates:
            return best
        for depth in range(1, 25):
            scores = {}
            alpha = -2*WIN
            current = None
            try:
                for _, code, child, _, _ in candidates:
                    cp,cq,cg,cog,cc,coc,ch,cv,cn = child
                    # Prefer a forward move to spending a wall for an equal score.
                    bonus = self.progress*(f.dist(p)-f.dist(code)) if code < 81 else 0
                    bonus -= move_risk.get(code,0)
                    value = self.child_value((cq,cp,cog,cg,coc,cc,ch,cv,cn),depth-1,
                                             alpha-bonus,2*WIN,1,current is None)+bonus
                    scores[code] = value
                    if current is None or value > alpha:
                        alpha, current = value, code
            except SearchTimeout:
                break
            if current is not None:
                best = action(current)
                self.depth_reached = depth
                self.last_score = alpha
            candidates.sort(key=lambda item: -scores.get(item[1],-2*WIN))
            if alpha > WIN-1000:
                break
            if alpha < -WIN+1000:
                # A losing principal variation can just repeat against a
                # weaker opponent. Seek a practical escape instead of delay.
                viable = [item for item in candidates
                          if not any(m//9 == og for m in moves(q,item[2][0],item[2][8]))]
                if viable:
                    best = action(max(viable,key=lambda item: item[0])[1])
                break
        return best


def action(code):
    if code < 81:
        return {'type':'move','to':[int(code//9),int(code%9)]}
    w = code-81
    _, _, r, c = WALLS[w]
    return {'type':'wall','orientation':'H' if w<64 else 'V','row':int(r),'col':int(c)}


def fallback_sprint(obs):
    try:
        legal = obs.get('legal_moves') or []
        if not legal:
            return {'type':'pass'}
        dist = obs.get('distances')
        target = min(legal, key=lambda m: (dist[m[0]][m[1]] if dist and dist[m[0]][m[1]]>=0 else INF,abs(m[1]-4)))
        return {'type':'move','to':[int(target[0]),int(target[1])]}
    except Exception:
        return {'type':'pass'}


def opening_tempo(obs, deadline):
    """Use a useful waiting wall rather than concede a free opening jump."""
    if obs['walls']['horizontal'] or obs['walls']['vertical']:
        return None
    if obs['you']['walls_remaining'] <= 0:
        return None
    pr,pc = obs['you']['position']
    qr,qc = obs['opponent']['position']
    if not ((pc==qc and abs(pr-qr)==2) or (pr==qr and abs(pc-qc)==2)):
        return None
    middle = [(pr+qr)//2,(pc+qc)//2]
    if middle not in obs['legal_moves']:
        return None
    solver = Solver(obs,deadline)
    solver.root_wall_key = (0,0)
    p,q = pr*9+pc,qr*9+qc
    g,og = obs['you']['goal_row'],obs['opponent']['goal_row']
    f,of = solver.field(0,0,g,INITIAL),solver.field(0,0,og,INITIAL)
    dp,dq = f.dist(p),of.dist(q)
    legal = {(0 if w['orientation']=='H' else 64)+w['row']*8+w['col'] for w in obs['legal_walls']}
    candidates = solver.walls(p,q,g,og,obs['you']['walls_remaining'],
                              obs['opponent']['walls_remaining'],0,0,INITIAL,f,of,128,legal)
    useful = [item for item in candidates if item[3].dist(p)==dp and item[4].dist(q)>dq]
    if useful:
        return action(max(useful,key=lambda item:(item[4].dist(q)-dq,item[0],-item[1]))[1])
    return None


def choose_action(observation):
    start = time.perf_counter()
    try:
        fallback = fallback_sprint(observation)
        for r,c in observation.get('legal_moves',[]):
            if r == observation['you']['goal_row']:
                return {'type':'move','to':[int(r),int(c)]}
        opening = opening_tempo(observation,start+TIME_LIMIT)
        if opening is not None:
            return opening
        return Solver(observation,start+TIME_LIMIT).choose(observation,fallback)
    except Exception:
        return fallback_sprint(observation)
