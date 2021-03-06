'''
Created on Sep 5, 2018

Module for measuring LTL formulas.

@author: Marten Lohstroh
'''
import re
from copy import copy, deepcopy
from sys import argv, setrecursionlimit
from spec_space.parser.parser import LTL_PARSER
from spec_space.formula import TrueFormula, FalseFormula, Constant, Next, \
        VarNext, Disjunction, Conjunction, UnaryFormula, Literal, \
        BinaryFormula, Globally, Eventually, DoubleImplication, Implication, \
        Negation, Until, WeakUntil, Release;
from pyeda.boolalg.expr import expr, DimacsCNF
from pyeda.inter import expr2truthtable
from subprocess import call, check_output
from spec_space.symbol_sets import PyEDASymbolSet
#import hashlib

expr1 = None
expr2 = None
N = None

cache = {}

bypass_count = True
do_memoize = True

source = PyEDASymbolSet()
target = PyEDASymbolSet()

fls = target.symbols['FALSE']
tru = target.symbols['TRUE']
lor = target.symbols['OR']
lnd = target.symbols['AND']
neg = target.symbols['NOT']

setrecursionlimit(100000)

''' Maps atomic propositions to sets of time indexes. '''
class DepTracker:

    ''' Constructor. '''
    def __init__(self, literal=None, indexes=None):
        self.literals = {}
        if literal != None and indexes != None:
            self.add(literal, indexes)

    ''' Add an AP to the tracker, map it to given set of indexes.  '''
    def add(self, literal, indexes):
        if self.literals.get(literal) == None:
            self.literals[literal] = indexes
        else:
            self.literals[literal] = self.literals[literal].union(indexes)

    ''' Calculate the union of this tracker and another. '''
    def union(self, other):
        new = DepTracker()
        if other == None:
            return new
        for k,v in other.literals.items():
            if self.literals.get(k) == None:
                new.add(k, v)
            else:
                new.add(k, self.literals[k].union(v))
        for k,v in self.literals.items():
            if other.literals.get(k) == None:
                new.add(k, v)

        return new

    ''' Determine whether the intersection of tracked literals between this 
        tracker and another is empty. Return true iff emtpy. '''
    def isdisjoint(self, other):
        if len(self.literals) <= len(other.literals):
            mine = self.literals
            their = other.literals
        else:
            mine = other.literals
            their = self.literals

        for k,v in mine.items():
            if their.get(k) != None:
                return False
        return True

    ''' Count the number of tracked variables. '''
    def count(self):
        cnt = 0
        for v in self.literals.values():
            for t in v:
                cnt += 1
        return cnt

    ''' Test whether each literal is tracked for at most one time index. '''    
    def timeindependent(self):
        for v in self.literals.values():
            if len(v) > 1:
                return False
        return True

    ''' Produce a shifted version of this tracker; time index of each tracked
        literal is increased by n. '''
    def shifted(self, n):
        new = DepTracker()
        for k, v in self.literals.items():
            indexes = set([])
            for t in v:
                if (t+n <= N):
                    indexes.add(t+n)    
            new.add(k, indexes)
        return new

    ''' Produce a saturated version of this tracker; each literal will be tracked
        from its smallest time index up to the given time bound (default=N). '''
    def saturated(self, time_bound=None):
        new = DepTracker()
        if time_bound == None:
            time_bound = N
        for k, v in self.literals.items():
            new.add(k, set(range(min(v), time_bound+1)))
        return new

''' Expand a given LTL formula into a Boolean expression, observing time bound N. 
    Returns a string representation of the expansion. Some basic constant folding 
    is done to eradicate trivial bloat in the generated expression. '''
def expand(f, n=0):

    if isinstance(f, Literal):
        if (n > N):
            return fls        
        else:
            name = f.generate(with_base_names=True)
        return name + str(n)

    if isinstance(f, BinaryFormula):
        
        l = expand(f.left_formula, n)
        r = expand(f.right_formula, n)
        
        if isinstance(f, Conjunction):
            if (l == fls or r == fls):
                return fls
            elif (l == tru):
                return r
            elif (r == tru):
                return l
            elif (r == tru and l == tru):
                return tru
            else:
                return "(" + l + " " + lnd + " " + r + ")"
        elif isinstance(f, Disjunction):
            if (l == fls and r == fls):
                return fls
            elif (l == fls):
                return r
            elif (r == fls):
                return l
            else:
                return "(" + l + " " + lor + " " + r + ")"
        elif isinstance(f, Until):
            first = f.left_formula
            then = f.right_formula

            disj = then
            for j in range(N):
                conj = first
                elem = first
                out = Next(then)
                for i in range(j):
                    conj = Conjunction(conj, Next(elem))
                    elem = Next(elem)
                    out = Next(out)

                conj = Conjunction(conj, out)
                disj = Disjunction(disj, conj)
            return expand(disj)

            raise Exception("Error: cannot expand unsupported BinaryFormula!")
        else:
            raise Exception("Error: cannot expand unsupported BinaryFormula!")
    else:
        if isinstance(f, Globally):
            conj = "(" + tru
            while (n <= N):
                e = expand(f.right_formula, n)
                if e == fls:
                    return fls
                else:
                    conj += " " + lnd + " " + e
                n += 1
            return conj + ")"
        elif isinstance(f, Eventually):
            disj = "(" + fls
            while (n <= N):
                e = expand(f.right_formula, n)
                if e != fls:
                    disj += " " + lor + " " + e 
                n += 1
            return disj + ")"
        elif isinstance(f, Next) or isinstance(f, VarNext):
            return expand(f.right_formula, n+1)
        elif isinstance(f, Negation):
            e = expand(f.right_formula, n)
            if e == fls:
                return tru
            elif e == fls:
                return fls
            else:
                return neg + e
        else:
            if f.right_formula != None:
                raise Exception("Not expanding the tree for AST node: " + type(f).__name__)
            
            return f


''' Print a help message and exit. '''
def help_exit():
    print("Usage: python measure.py [-d] [TIME_BOUND] LTL_EXPR1 [LTL_EXPR2]")
    exit(1)

''' Read commandline arguments. '''
def init():
    global N, expr1, expr2, bypass_count
    
    offset = 0
    if (len(argv) > 2):
        if argv[1] == "-d":
            bypass_count = False
            offset = 1

    try:
        N = int(argv[offset +1])
    except Exception as e:
         help_exit()

    try:
        expr1 = LTL_PARSER.parse(argv[offset+2], symbol_set_cls=source)
        if len(argv) > offset+3:
            expr2 = LTL_PARSER.parse(argv[offset+3], symbol_set_cls=source)
    except Exception as e:
        help_exit()

    if N == None or expr1 == None:
        help_exit()

''' Pass the given Boolean formula to SharpSAT. 
    Return the number of satisfying models devided by 2**nvars. '''
def sat_measure(formula):
    cnf = expr(formula).to_cnf()
    #print(cnf)
    ''' False '''
    if str(cnf) == "0":
        return 0
    ''' True '''
    if str(cnf) == "1":
        return 1
    else:
        ''' Sat? '''
        file = open('input.cnf', 'w')
        litmap, nvars, clauses = cnf.encode_cnf()
        dimacs = str(DimacsCNF(nvars, clauses))
        
        if cache.get(dimacs) != None and do_memoize:
            return cache[dimacs]
        else:
            file.write(dimacs)
            file.close()

            output = check_output(["bin/sharpSAT", "input.cnf"])
            m = re.search(r"# solutions \n([0-9]+)\n# END", output.decode('UTF-8'))
            #print(m.group(1))
            print("vars: " + str(nvars))
            print("clauses: " + (str(len(clauses))))
            # print(str(DimacsCNF(nvars, clauses)))
            cache[dimacs] = int(m.group(1))/2**nvars 
            return cache[dimacs]

''' Traversal function that reduces implications, double implications. '''
def simplify(f):

    if isinstance(f, BinaryFormula):
        l = f.left_formula
        r = f.right_formula

        if isinstance(f, Implication):
            if (l == FalseFormula or r == TrueFormula):
                return TrueFormula()
            if (l == TrueFormula):
                return r
            if (r == FalseFormula):
                return Negation(l)
            else:
                return Disjunction(Negation(l),r)
        
        if isinstance(f, DoubleImplication):
            return Disjunction(Conjunction(l, r), Conjunction(Negation(l), Negation(r)))
            # ((l and r) or (not l and not r))
            # FIXME: add reductions here
        
        # ψ W (ψ ∧ φ)
        if isinstance(f, Release):
            return simplify(WeakUntil(f.right_formula, Conjunction(f.left_formula, f.right_formula)))

        # (φ U ψ) ∨ G φ
        if isinstance(f, WeakUntil):
            return Disjunction(simplify(Until(f.left_formula, f.right_formula)), Globally(f.left_formula))

    return f

''' Recursively apply given function to each node in the AST. '''
def traverse(form, func):
    
    if isinstance(form, BinaryFormula):
        form.left_formula = traverse(form.left_formula, func)
        form.right_formula = traverse(form.right_formula, func)
    elif isinstance(form, UnaryFormula):
        form.right_formula = traverse(form.right_formula, func)
    
    return func(form)

''' Traversal function that computes AST nodes' dependencies. 
    Updates the info['deps'] field for all nodes, and the 
    info['lrdisjoint'] for bifurcating nodes. '''
def compute_deps(f):

    if isinstance(f, Literal):
        name = f.generate(with_base_names=True)
        f.info['deps'] = DepTracker(name, set([0]))
    elif isinstance(f, TrueFormula) or isinstance(f, FalseFormula):
        f.info['deps'] = DepTracker()
    elif isinstance(f, BinaryFormula):
        if isinstance(f, Until):
            ldeps = f.left_formula.info['deps'].saturated(max(N-1, 0))
            rdeps = f.right_formula.info['deps'].saturated(N)
        elif isinstance(f, Conjunction) or isinstance(f, Disjunction):
            ldeps = f.left_formula.info['deps']
            rdeps = f.right_formula.info['deps']
        else:
            raise Exception("Unsupported AST node: " + type(f).__name__)
        f.info['deps'] = ldeps.union(rdeps)
        f.info['lrdisjoint'] = ldeps.isdisjoint(rdeps)
    else:
        if isinstance(f, Globally) or isinstance(f, Eventually):
            f.info['deps'] = f.right_formula.info['deps'].saturated()
        elif isinstance(f, Next) or isinstance(f, VarNext): # check whether X could be parameterized
            f.info['deps'] = f.right_formula.info['deps'].shifted(1)
        elif isinstance(f, Negation):
            f.info['deps'] = f.right_formula.info['deps']
        else:
            raise Exception("Unsupported AST node: " + type(f).__name__)
    
    return f

''' Measure the given formula. '''
def measure(f, n=0):
    #print(f.generate(with_base_names=False))
    global bypass_count
    if isinstance(f, TrueFormula):
        return 1

    if isinstance(f, FalseFormula):
        return 0

    if isinstance(f, Literal):
        if (n <= N):
            return 0.5
        else:
            return 0

    if isinstance(f, Negation):
        return 1 - measure(f.right_formula, n)

    if isinstance(f, Conjunction):
        if f.info['lrdisjoint'] and bypass_count:
            #print("disjoint")
            return measure(f.right_formula, n) * measure(f.left_formula, n)
        else:
            #print("conjoint")
            return sat_measure(expand(f, n))

    if isinstance(f, Disjunction):
        if f.info['lrdisjoint'] and bypass_count:
            #print("disjoint")
            return 1 - (1-measure(f.right_formula, n)) * (1-measure(f.left_formula, n))
        else:
            #print("conjoint")
            return sat_measure(expand(f, n))

    if isinstance(f, Next):
        return measure(f.right_formula, n+1)

    if isinstance(f, Until):
        if f.info['lrdisjoint'] \
                and f.info['deps'].timeindependent() \
                and bypass_count:
            first = measure(f.left_formula)
            then = measure(f.right_formula)
            #print("a: " + str(first))
            #print("b: " + str(then))
            acc = then
            for i in range(N+1):
                #print("acc: " + str(acc))
                acc = 1-(1-acc*first)*(1-then)
            return acc
        else:
            return sat_measure(expand(f, n))

    if isinstance(f, Globally):
        deps = f.right_formula.info['deps']
        #print("rfdeps: " + str(deps.literals))
        if deps.timeindependent():
            #print("here")
            m = 1
            for i in range(0, N+1):
                m *= measure(f.right_formula, n+i) # we will easily move past N here.
            return m
        else:
            num_vars = f.info['deps'].count()
            return sat_measure(expand(f, n))

    if isinstance(f, Eventually):
        deps = f.right_formula.info['deps']
        #print("rfdeps: " + str(deps.literals))
        if deps.timeindependent():
            #print("here")
            m = 1
            for i in range(0, N+1):
                m *= 1 - measure(f.right_formula, n+i) # we will easily move past N here.
            return 1-m
        else:
            num_vars = f.info['deps'].count()
            return sat_measure(expand(f, n))

''' Main '''
init()

if (expr2 == None):
    #print("Expression:" + expr1.generate(with_base_names=False))
    expr1 = traverse(expr1, simplify)
    #print(expr1.generate(target, True))
    expr1 = traverse(expr1, compute_deps)
    #print("Expression:" + expr1.generate(with_base_names=False))
    print(measure(expr1))
else:
    print("Distance")
    diff = Disjunction(Conjunction(expr1, Negation(expr2)), Conjunction(Negation(expr1), expr2))
    diff = traverse(diff, simplify)
    diff = traverse(diff, compute_deps)
    print(measure(diff))
