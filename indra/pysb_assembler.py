import itertools
from sets import ImmutableSet

from pysb import (Model, Monomer, Parameter, Rule, Annotation,
        ComponentDuplicateNameError, ANY)
from pysb.core import SelfExporter
import pysb.export
from bel import bel_api
from biopax import biopax_api
from trips import trips_api
from indra import statements as ist

SelfExporter.do_export = False

# BaseAgent classes ####################################################

class BaseAgentSet(object):
    """Container for a set of BaseAgents.

    Wraps a dict of BaseAgent instances.
    """

    def __init__(self):
        self.agents = {}

    def get_create_base_agent(self, agent):
        """Return agent with given name, creating it if needed."""
        try:
            base_agent = self.agents[agent.name]
        except KeyError:
            base_agent = BaseAgent(agent.name)
            self.agents[agent.name] = base_agent

        if agent.bound_to:
            bound_to = self.get_create_base_agent(ist.Agent(agent.bound_to))
            bound_to.create_site(get_binding_site_name(agent.name))
            base_agent.create_site(get_binding_site_name(agent.bound_to))

        # There might be overwrites here
        for db_name, db_ref in agent.db_refs.iteritems():
            base_agent.db_refs[db_name] = db_ref

        return base_agent

    def iteritems(self):
        return self.agents.iteritems()

    def __getitem__(self, name):
        return self.agents[name]

class BaseAgent(object):
    def __init__(self, name):
        self.name = name
        self.sites = []
        self.site_states = {}
        # The list of site/state configurations that lead to this agent
        # being active (where the agent is currently assumed to have only
        # one type of activity)
        self.activating_mods = []
        self.db_refs = {}

    def create_site(self, site, states=None):
        """Create a new site on an agent if it doesn't already exist"""
        if site not in self.sites:
            self.sites.append(site)
        if states is not None:
            self.site_states.setdefault(site, [])
            try:
                states = list(states)
            except TypeError:
                return
            self.add_site_states(site, states)

    def add_site_states(self, site, states):
        """Create new states on a agent site if the site doesn't exist"""
        for state in states:
            if state not in self.site_states[site]:
                self.site_states[site].append(state)

    def add_activating_modification(self, activity_pattern):
        self.activating_mods.append(activity_pattern)

# Site/state information ###############################################

abbrevs = {
    'PhosphorylationSerine': 'S',
    'PhosphorylationThreonine': 'T',
    'PhosphorylationTyrosine': 'Y',
    'Phosphorylation': 'phospho',
    'Ubiquitination': 'ub',
    'Farnesylation': 'farnesyl',
    'Hydroxylation': 'hydroxyl',
    'Acetylation': 'acetyl',
    'Sumoylation': 'sumo',
    'Glycosylation': 'glycosyl',
    'Methylation': 'methyl',
    'Modification': 'mod',
}

active_site_names = {
    'Kinase': 'kin_site',
    'Phosphatase': 'phos_site',
    'GtpBound': 'switch',
    'Catalytic': 'cat_site',
}

states = {
    'PhosphorylationSerine': ['u', 'p'],
    'PhosphorylationThreonine': ['u', 'p'],
    'PhosphorylationTyrosine': ['u', 'p'],
    'Phosphorylation': ['u', 'p'],
    'Ubiquitination': ['n', 'y'],
    'Farnesylation': ['n', 'y'],
    'Hydroxylation': ['n', 'y'],
    'Acetylation': ['n', 'y'],
    'Sumoylation': ['n', 'y'],
    'Glycosylation': ['n', 'y'],
    'Methylation': ['n', 'y'],
    'Modification': ['n', 'y'],
}

# The following dict specifies the default modification/binding site names for
# modifications resulting from a particular type of activity. For example, a
# protein with Kinase activity makes a modification of type "phospho" on its
# substrate, and a RasGTPase (with GtpBound activity) binds to a site of type
# "RBD" (Ras binding domain). This comes in handy for specifying
# ActivityActivity rules, where the modification site mediating the activation
# is not specified.
default_mod_site_names = {
    'Kinase': 'phospho',
    'GtpBound': 'RBD',
    'Phosphatase': 'phospho',
}

def get_binding_site_name(name):
    binding_site = name.lower()
    return binding_site

def site_name(stmt):
    """Return all site names for a modification-type statement."""
    names = []
    if isinstance(stmt.mod, (list, tuple)):
        for m, mp in zip(stmt.mod, stmt.mod_pos):
            mod = abbrevs[m]
            mod_pos = mp if mp is not None else ''
            names.append('%s%s' % (mod, mod_pos))
    else:
        mod = abbrevs[stmt.mod]
        mod_pos = stmt.mod_pos if stmt.mod_pos is not None else ''
        names.append('%s%s' % (mod, mod_pos))

    return names

def get_activating_mods(agent, agent_set):
    act_mods = agent_set[agent.name].activating_mods
    if not act_mods:
        act_mods = [{}]
    return act_mods

# PySB model elements ##################################################

def add_rule_to_model(model, rule):
    try:
        model.add_component(rule)
    # If this rule is already in the model, issue a warning and continue
    except ComponentDuplicateNameError:
        msg = "Rule %s already in model! Skipping." % rule.name
        warnings.warn(msg)

def get_create_parameter(model, name, value, unique=True):
    """Return parameter with given name, creating it if needed.

    If unique is false and the parameter exists, the value is not changed; if
    it does not exist, it will be created. If unique is true then upon conflict
    a number is added to the end of the parameter name.
    """

    parameter = model.parameters.get(name)

    if not unique and parameter is not None:
        return parameter

    if unique:
        pnum = 1
        while True:
            pname = name + '_%d' % pnum
            if model.parameters.get(pname) is None:
                break
            pnum += 1
    else:
        pname = name

    parameter = Parameter(pname, value)
    model.add_component(parameter)
    return parameter

def get_complex_pattern(model, agent, agent_set, extra_fields=None):
    '''
    Constructs a PySB ComplexPattern from an Agent
    '''
    monomer = model.monomers[agent.name]

    pattern = {}

    if extra_fields is not None:
        for k,v in extra_fields.iteritems():
            pattern[k] = v
    if agent.bound_to:
        # Here we make the assumption that the binding site
        # is simply named after the binding partner
        if agent.bound_neg:
            pattern[get_binding_site_name(agent.bound_to)] = None
        else:
            pattern[get_binding_site_name(agent.bound_to)] = ANY

    # Add the pattern for the modifications of the agent
    # TODO: This is specific to phosphorylation but we should be
    # able to support other types as well
    for m, mp in zip(agent.mods, agent.mod_sites):
        mod = abbrevs[m]
        mod_pos = mp if mp is not None else ''
        mod_site = ('%s%s' % (mod, mod_pos))
        pattern[mod_site] = 'p'

    complex_pattern = monomer(**pattern)
    return complex_pattern

def add_default_initial_conditions(model):
    # Iterate over all monomers
    for m in model.monomers:
        set_base_initial_condition(model, m, 100.0)

def set_base_initial_condition(model, monomer, value):
    # Build up monomer pattern dict
    sites_dict = {}
    for site in monomer.sites:
        if site in monomer.site_states:
            sites_dict[site] = monomer.site_states[site][0]
        else:
            sites_dict[site] = None
    mp = monomer(**sites_dict)
    pname = monomer.name + '_0'
    try:
        p = model.parameters[pname]
        p.value = value
    except KeyError:
        p = Parameter(pname, value)
        model.add_component(p)
        model.initial(mp, p)

def get_annotation(component, db_name, db_ref):
    '''
    Construct Annotation following format guidelines
    given at http://identifiers.org/.
    '''
    url = 'http://identifiers.org/'
    subj = component
    if db_name == 'UP':
        obj = url + 'uniprot/%s' % db_ref
        pred = 'is'
    elif db_name == 'HGNC':
        obj = url + 'hgnc/HGNC:%s' % db_ref
        pred = 'is'
    elif db_name == 'XFAM' and db_ref.startswith('PF'):
        obj = url + 'pfam/%s' % db_ref
        pred = 'is'
    elif db_name == 'CHEBI':
        obj = url + 'chebi/CHEBI:%s' % db_ref
        pred = 'is'
    else:
        return None
    return Annotation(subj, obj, pred)

# PysbAssembler #######################################################

class UnknownPolicyError(Exception):
    pass

class PysbAssembler(object):
    def __init__(self, policies=None):
        self.statements = []
        self.agent_set = None
        self.model = None
        self.policies = policies

    def statement_exists(self, stmt):
        for s in self.statements:
            if stmt == s:
                return True
        return False

    def add_statements(self, stmts):
        for stmt in stmts:
            if not self.statement_exists(stmt):
                self.statements.append(stmt)

    def dispatch(self, stmt, stage, *args):
        class_name = stmt.__class__.__name__
        if not self.policies:
            policy = 'default'
        else:
            policy = policies.get(class_name, 'default')
        func_name = '%s_%s_%s' % (class_name.lower(), stage, policy)
        func = globals().get(func_name)
        if func is None:
            raise UnknownPolicyError('%s function %s not defined' %
                                     (stage, func_name))
        return func(stmt, *args)

    def monomers(self):
        """Calls the appropriate monomers method based on policies."""
        for stmt in self.statements:
            self.dispatch(stmt, 'monomers', self.agent_set)

    def assemble(self):
        for stmt in self.statements:
            self.dispatch(stmt, 'assemble', self.model, self.agent_set)

    def make_model(self, initial_conditions=True):
        self.model = Model()
        self.agent_set = BaseAgentSet()
        # Collect information about the monomers/self.agent_set from the
        # statements
        self.monomers()
        # Add the monomers to the model based on our BaseAgentSet
        for agent_name, agent in self.agent_set.iteritems():
            m = Monomer(agent_name, agent.sites, agent.site_states)
            self.model.add_component(m)
            for db_name, db_ref in agent.db_refs.iteritems():
                a = get_annotation(m, db_name, db_ref)
                if a is not None:
                    self.model.add_annotation(a)
        # Iterate over the statements to generate rules
        self.assemble()
        # Add initial conditions
        if initial_conditions:
            add_default_initial_conditions(self.model)
        return self.model

    def print_model(self, fname='pysb_model.py'):
        if self.model is not None:
            with open(fname, 'wt') as fh:
                fh.write(pysb.export.export(self.model, 'pysb_flat'))

    def print_rst(self, fname='pysb_model.rst', module_name='pysb_module'):
        if self.model is not None:
            with open(fname, 'wt') as fh:
                fh.write('.. _%s:\n\n' % module_name)
                fh.write('Module\n======\n\n')
                fh.write('INDRA-assembled model\n---------------------\n\n')
                fh.write('::\n\n')
                model_str = pysb.export.export(self.model, 'pysb_flat')
                model_str = '\t' + model_str.replace('\n', '\n\t')
                fh.write(model_str)

# COMPLEX ############################################################

def complex_monomers_one_step(stmt, agent_set):
    """In this (very simple) implementation, proteins in a complex are
    each given site names corresponding to each of the other members
    of the complex (lower case). So the resulting complex can be
    "fully connected" in that each member can be bound to
    all the others."""
    for i, member in enumerate(stmt.members):
        gene_mono = agent_set.get_create_base_agent(member)
        # Add sites for agent modifications
        # TODO: This assumes phosphorylation, but in principle
        # it could be some other modification
        for m, mp in zip(member.mods, member.mod_sites):
            mod = abbrevs[m]
            mod_pos = mp if mp is not None else ''
            mod_site = ('%s%s' % (mod, mod_pos))
            gene_mono.create_site(mod_site, ['u', 'p'])

        # Specify a binding site for each of the other complex members
        # bp = abbreviation for "binding partner"
        for j, bp in enumerate(stmt.members):
            # The protein doesn't bind to itstmt!
            if i == j:
                continue
            gene_mono.create_site(get_binding_site_name(bp.name))

complex_monomers_interactions_only = complex_monomers_one_step
complex_monomers_default = complex_monomers_one_step

def complex_assemble_one_step(stmt, model, agent_set):
    pairs = itertools.combinations(stmt.members, 2)
    for pair in pairs:
        agent1 = pair[0]
        agent2 = pair[1]
        param_name = agent1.name[0].lower() +\
                     agent2.name[0].lower() + '_bind'
        kf_bind = get_create_parameter(model, 'kf_' + param_name, 1e-6)
        kr_bind = get_create_parameter(model, 'kr_' + param_name, 1e-6)

        # Make a rule name
        name_components = []
        for m in pair:
            if m.bound_to:
                if m.bound_neg:
                    name_components.append(m.name + '_n' + m.bound_to)
                else:
                    name_components.append(m.name + '_' + m.bound_to)
            else:
                name_components.append(m.name)

        # Construct full patterns of each agent with conditions
        rule_name = '_'.join(name_components) + '_bind'
        agent1_pattern = get_complex_pattern(model, agent1, agent_set)
        agent2_pattern = get_complex_pattern(model, agent2, agent_set)
        agent1_bs = get_binding_site_name(agent2.name)
        agent2_bs = get_binding_site_name(agent1.name)
        r = Rule(rule_name, agent1_pattern(**{agent1_bs: None}) +\
                            agent2_pattern(**{agent2_bs: None}) >>
                            agent1_pattern(**{agent1_bs: 1}) %\
                            agent2_pattern(**{agent2_bs: 1}),
                            kf_bind)
        add_rule_to_model(model, r)

        # In reverse reaction, assume that dissocition is unconditional
        rule_name = '_'.join(name_components) + '_dissociate'
        agent1_uncond = get_complex_pattern(model, ist.Agent(agent1.name),
                                            agent_set)
        agent2_uncond = get_complex_pattern(model, ist.Agent(agent2.name),
                                            agent_set)
        r = Rule(rule_name, agent1_uncond(**{agent1_bs: 1}) %\
                            agent2_uncond(**{agent2_bs: 1}) >>
                            agent1_uncond(**{agent1_bs: None}) +\
                            agent2_uncond(**{agent2_bs: None}),
                            kr_bind)
        add_rule_to_model(model, r)

def complex_assemble_multi_way(stmt, model, agent_set):
    # Get the rate parameter
    abbr_name = ''.join([m.name[0].lower() for m in stmt.members])
    kf_bind = get_create_parameter(model, 'kf_' + abbr_name + '_bind', 1e-6)
    kr_bind = get_create_parameter(model, 'kr_' + abbr_name + '_bind', 1e-6)

    # Make a rule name
    name_components = []
    for m in stmt.members:
        if m.bound_to:
            if m.bound_neg:
                name_components.append(m.name + '_n' + m.bound_to)
            else:
                name_components.append(m.name + '_' + m.bound_to)
        else:
            name_components.append(m.name)
    rule_name = '_'.join(name_components) + '_bind'
    # Initialize the left and right-hand sides of the rule
    lhs = ReactionPattern([])
    rhs = ComplexPattern([], None)
    # We need a unique bond index for each pair of proteins in the
    # complex, resulting in n(n-1)/2 bond indices for a n-member complex.
    # We keep track of the bond indices using the bond_indices dict,
    # which maps each unique pair of members to a bond index.
    bond_indices = {}
    bond_counter = 1
    for i, member in enumerate(stmt.members):
        gene_name = member.name
        mono = model.monomers[gene_name]
        # Specify free and bound states for binding sites for each of
        # the other complex members
        # (bp = abbreviation for "binding partner")
        left_site_dict = {}
        right_site_dict = {}
        for j, bp in enumerate(stmt.members):
            bp_bs = get_binding_site_name(bp.name)
            # The protein doesn't bind to itstmt!
            if i == j:
                continue
            # Check to see if we've already created a bond index for these
            # two binding partners
            bp_set = ImmutableSet([i, j])
            if bp_set in bond_indices:
                bond_ix = bond_indices[bp_set]
            # If we haven't see this pair of proteins yet, add a new bond
            # index to the dict
            else:
                bond_ix = bond_counter
                bond_indices[bp_set] = bond_ix
                bond_counter += 1
            # Fill in the entries for the site dicts
            left_site_dict[bp_bs] = None
            right_site_dict[bp_bs] = bond_ix

        # Add the pattern for the modifications of the member
        # TODO: This is specific to phosphorylation but we should be
        # able to support other types as well
        for m, mp in zip(member.mods, member.mod_sites):
            mod = abbrevs[m]
            mod_pos = mp if mp is not None else ''
            mod_site = ('%s%s' % (mod, mod_pos))
            left_site_dict[mod_site] = 'p'
            right_site_dict[mod_site] = 'p'

        # Add the pattern for the member being bound
        if member.bound_to:
            bound_name = member.bound_to
            bound_bs = get_binding_site_name(bound_name)
            gene_bs = get_binding_site_name(gene_name)
            if member.bound_neg:
                left_site_dict[bound_bs] = None
                right_site_dict[bound_bs] = None
                left_pattern = mono(**left_site_dict)
                right_pattern = mono(**right_site_dict)
            else:
                bound = model.monomers[bound_name]
                left_site_dict[bound_bs] =\
                    bond_counter
                right_site_dict[bound_bs] =\
                    bond_counter
                left_pattern = mono(**left_site_dict) % \
                                bound(**{gene_bs:bond_counter})
                right_pattern = mono(**right_site_dict) % \
                                bound(**{gene_bs:bond_counter})
                bond_counter += 1
        else:
            left_pattern = mono(**left_site_dict)
            right_pattern = mono(**right_site_dict)
        # Build up the left- and right-hand sides of the rule from
        # monomer patterns with the appropriate site dicts
        lhs = lhs + left_pattern
        rhs = rhs % right_pattern
    # Finally, create the rule and add it to the model
    rule = Rule(rule_name, lhs <> rhs, kf_bind, kr_bind)
    add_rule_to_model(model, rule)

complex_assemble_interactions_only = complex_assemble_one_step
complex_assemble_default = complex_assemble_one_step

# PHOSPHORYLATION ###################################################

def phosphorylation_monomers_interactions_only(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    enz.create_site(active_site_names['Kinase'])
    sub = agent_set.get_create_base_agent(stmt.sub)
    # See NOTE in monomers_one_step, below
    sub.create_site(site_name(stmt)[0], ('u', 'p'))

def phosphorylation_monomers_one_step(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    sub = agent_set.get_create_base_agent(stmt.sub)
    # NOTE: This assumes that a Phosphorylation statement will only ever
    # involve a single phosphorylation site on the substrate (typically
    # if there is more than one site, they will be parsed into separate
    # Phosphorylation statements, i.e., phosphorylation is assumed to be
    # distributive. If this is not the case, this assumption will need to
    # be revisited.
    sub.create_site(site_name(stmt)[0], ('u', 'p'))

def phosphorylation_monomers_two_step(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    sub = agent_set.get_create_base_agent(stmt.sub)
    sub.create_site(site_name(stmt)[0], ('u', 'p'))

    # Create site for binding the substrate
    enz.create_site(get_binding_site_name(sub.name))
    sub.create_site(get_binding_site_name(enz.name))

phosphorylation_monomers_default = phosphorylation_monomers_one_step

def phosphorylation_assemble_interactions_only(stmt, model, agent_set):
    kf_bind = get_create_parameter(model, 'kf_bind', 1.0, unique=False)
    kr_bind = get_create_parameter(model, 'kr_bind', 1.0, unique=False)

    enz = model.monomers[stmt.enz.name]
    sub = model.monomers[stmt.sub.name]

    # See NOTE in monomers_one_step
    site = site_name(stmt)[0]

    rule_name = '%s_phospho_%s_%s' % (stmt.enz.name, stmt.sub.name, site)
    active_site = active_site_names['Kinase']
    # Create a rule specifying that the substrate binds to the kinase at
    # its active site
    r = Rule(rule_name,
                enz(**{active_site:None}) + sub(**{site:None}) <>
                enz(**{active_site:1}) + sub(**{site:1}),
                kf_bind, kr_bind)
    add_rule_to_model(model, r)

def phosphorylation_assemble_one_step(stmt, model, agent_set):
    param_name = 'kf_' + stmt.enz.name[0].lower() +\
                    stmt.sub.name[0].lower() + '_phos'
    kf_phospho = get_create_parameter(model, param_name, 1e-6)

    # See NOTE in monomers_one_step
    site = site_name(stmt)[0]

    enz_pattern = get_complex_pattern(model, stmt.enz, agent_set)
    sub_unphos = get_complex_pattern(model, stmt.sub, agent_set, 
        extra_fields={site: 'u'})
    sub_phos = get_complex_pattern(model, stmt.sub, agent_set, 
        extra_fields={site: 'p'})

    enz_act_mods = get_activating_mods(stmt.enz, agent_set)
    for i, am in enumerate(enz_act_mods):
        rule_name = '%s_phospho_%s_%s_%d' %\
            (stmt.enz.name, stmt.sub.name, site, i+1)
        r = Rule(rule_name,
                enz_pattern(am) + sub_unphos >>
                enz_pattern(am) + sub_phos,
                kf_phospho)
        add_rule_to_model(model, r)

def phosphorylation_assemble_two_step(stmt, model, agent_set):
    sub_bs = get_binding_site_name(stmt.sub.name)
    enz_bound = get_complex_pattern(model, stmt.enz, agent_set,
        extra_fields = {sub_bs: 1})
    enz_unbound = get_complex_pattern(model, stmt.enz, agent_set,
        extra_fields = {sub_bs: None})
    sub_pattern = get_complex_pattern(model, stmt.sub, agent_set)

    param_name = ('kf_' + stmt.enz.name[0].lower() +
                  stmt.sub.name[0].lower() + '_bind')
    kf_bind = get_create_parameter(model, param_name, 1e-6)
    param_name = ('kr_' + stmt.enz.name[0].lower() +
                  stmt.sub.name[0].lower() + '_bind')
    kr_bind = get_create_parameter(model, param_name, 1e-3)
    param_name = ('kc_' + stmt.enz.name[0].lower() +
                  stmt.sub.name[0].lower() + '_phos')
    kf_phospho = get_create_parameter(model, param_name, 1e-3)

    site = site_name(stmt)[0]

    enz_act_mods = get_activating_mods(stmt.enz, agent_set)
    enz_bs = get_binding_site_name(stmt.enz.name)
    for i, am in enumerate(enz_act_mods):
        rule_name = '%s_phospho_bind_%s_%s_%d' %\
            (stmt.enz.name, stmt.sub.name, site, i+1)
        r = Rule(rule_name,
            enz_unbound(am) +\
            sub_pattern(**{site: 'u', enz_bs: None}) >>
            enz_bound(am) %\
            sub_pattern(**{site: 'u', enz_bs: 1}),
            kf_bind)
        add_rule_to_model(model, r)

        rule_name = '%s_phospho_%s_%s_%d' %\
            (stmt.enz.name, stmt.sub.name, site, i+1)
        r = Rule(rule_name,
            enz_bound(am) %\
                sub_pattern(**{site: 'u', enz_bs: 1}) >>
            enz_unbound(am) +\
                sub_pattern(**{site: 'p', enz_bs: None}),
            kf_phospho)
        add_rule_to_model(model, r)

    rule_name = '%s_dissoc_%s' % (stmt.enz.name, stmt.sub.name)
    r = Rule(rule_name, model.monomers[stmt.enz.name](**{sub_bs: 1}) %\
             model.monomers[stmt.sub.name](**{enz_bs: 1}) >>
             model.monomers[stmt.enz.name](**{sub_bs: None}) +\
             model.monomers[stmt.sub.name](**{enz_bs: None}), kr_bind)
    add_rule_to_model(model, r)

phosphorylation_assemble_default = phosphorylation_assemble_one_step

# CIS-AUTOPHOSPHORYLATION ###################################################

def autophosphorylation_monomers_interactions_only(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    enz.create_site(site_name(stmt)[0], ('u', 'p'))

def autophosphorylation_monomers_one_step(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    # NOTE: This assumes that a Phosphorylation statement will only ever
    # involve a single phosphorylation site on the substrate (typically
    # if there is more than one site, they will be parsed into separate
    # Phosphorylation statements, i.e., phosphorylation is assumed to be
    # distributive. If this is not the case, this assumption will need to
    # be revisited.
    enz.create_site(site_name(stmt)[0], ('u', 'p'))

autophosphorylation_monomers_default = autophosphorylation_monomers_one_step

def autophosphorylation_assemble_interactions_only(stmt, model, agent_set):
    stmt.assemble_one_step(model, agent_set)

def autophosphorylation_assemble_one_step(stmt, model, agent_set):
    param_name = 'kf_' + stmt.enz.name[0].lower() + '_autophos'
    kf_autophospho = get_create_parameter(model, param_name, 1e-3)

    # See NOTE in monomers_one_step
    site = site_name(stmt)[0]
    pattern_unphos = get_complex_pattern(model, stmt.enz, agent_set,
                                         extra_fields={site: 'u'})
    pattern_phos = get_complex_pattern(model, stmt.enz, agent_set,
                                       extra_fields={site: 'p'})
    rule_name = '%s_autophospho_%s_%s' % (stmt.enz.name, stmt.enz.name, site)
    r = Rule(rule_name, pattern_unphos >> pattern_phos, kf_autophospho)
    add_rule_to_model(model, r)

autophosphorylation_assemble_default = autophosphorylation_assemble_one_step

# TRANSPHOSPHORYLATION ###################################################

def transphosphorylation_monomers_interactions_only(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    # Assume there is exactly one bound_to species
    sub = agent_set.get_create_base_agent(stmt.enz)
    sub.create_site(site_name(stmt)[0], ('u', 'p'))

def transphosphorylation_monomers_one_step(stmt, agent_set):
    enz = agent_set.get_create_base_agent(stmt.enz)
    # NOTE: This assumes that a Phosphorylation statement will only ever
    # involve a single phosphorylation site on the substrate (typically
    # if there is more than one site, they will be parsed into separate
    # Phosphorylation statements, i.e., phosphorylation is assumed to be
    # distributive. If this is not the case, this assumption will need to
    # be revisited. 
    sub = agent_set.get_create_base_agent(ist.Agent(stmt.enz.bound_to))
    sub.create_site(site_name(stmt)[0], ('u', 'p'))

transphosphorylation_monomers_default = transphosphorylation_monomers_one_step

def transphosphorylation_assemble_interactions_only(stmt, model, agent_set):
    stmt.assemble_one_step(model, agent_set)

def transphosphorylation_assemble_one_step(stmt, model, agent_set):
    param_name = ('kf_' + stmt.enz.name[0].lower() +
                  stmt.enz.bound_to[0].lower() + '_transphos')
    kf  = get_create_parameter(model, param_name, 1e-3)

    site = site_name(stmt)[0]
    enz_pattern = get_complex_pattern(model, stmt.enz, agent_set)
    sub_unphos = get_complex_pattern(model, ist.Agent(stmt.enz.bound_to),
        agent_set, extra_fields = {site: 'u'})
    sub_phos = get_complex_pattern(model, ist.Agent(stmt.enz.bound_to),
        agent_set, extra_fields = {site: 'p'})

    rule_name = '%s_transphospho_%s_%s' % (stmt.enz.name,
                                           stmt.enz.bound_to, site)
    r = Rule(rule_name, enz_pattern % sub_unphos >>\
                    enz_pattern % sub_phos, kf)
    add_rule_to_model(model, r)

transphosphorylation_assemble_default = transphosphorylation_assemble_one_step

# ACTIVITYACTIVITY ###################################################### 

def activityactivity_monomers_interactions_only(self, agent_set):
    subj = agent_set.get_create_base_agent(self.subj)
    subj.create_site(active_site_names[self.subj_activity])
    obj = agent_set.get_create_base_agent(self.obj)
    obj.create_site(active_site_names[self.obj_activity])
    obj.create_site(default_mod_site_names[self.subj_activity])

def activityactivity_monomers_one_step(self, agent_set):
    subj = agent_set.get_create_base_agent(self.subj)
    subj.create_site(self.subj_activity, ('inactive', 'active'))
    obj = agent_set.get_create_base_agent(self.obj)
    obj.create_site(self.obj_activity, ('inactive', 'active'))

activityactivity_monomers_default = activityactivity_monomers_one_step

def activityactivity_assemble_interactions_only(self, model):
    kf_bind = get_create_parameter(model, 'kf_bind', 1.0, unique=False)
    subj = model.monomers[self.subj.name]
    obj = model.monomers[self.obj.name]
    subj_active_site = active_site_names[self.subj_activity]
    obj_mod_site = default_mod_site_names[self.subj_activity]
    r = Rule('%s_%s_activates_%s_%s' %
             (self.subj.name, self.subj_activity, self.obj.name,
              self.obj_activity),
             subj(**{subj_active_site: None}) +
             obj(**{obj_mod_site: None}) >>
             subj(**{subj_active_site: 1}) %
             obj(**{obj_mod_site: 1}),
             kf_bind)
    add_rule_to_model(model, r)

def activityactivity_assemble_one_step(self, model, agent_set):
    subj_pattern = get_complex_pattern(model, self.subj, agent_set, 
        extra_fields={self.subj_activity: 'active'})
    obj_inactive = get_complex_pattern(model, self.obj, agent_set, 
        extra_fields={self.obj_activity: 'inactive'})
    obj_active = get_complex_pattern(model, self.obj, agent_set, 
        extra_fields={self.obj_activity: 'active'})

    param_name = 'kf_' + self.subj.name[0].lower() +\
                        self.obj.name[0].lower() + '_act'
    kf_one_step_activate = \
                   get_create_parameter(model, param_name, 1e-6)

    rule_name = '%s_%s_activates_%s_%s' %\
        (self.subj.name, self.subj_activity, self.obj.name,
         self.obj_activity)

    if self.relationship == 'increases':
       r = Rule(rule_name,
            subj_pattern + obj_inactive >> subj_pattern + obj_active,
            kf_one_step_activate)
    else:
       r = Rule(rule_name,
            subj_pattern + obj_active >> subj_pattern + obj_inactive,
            kf_one_step_activate)

    add_rule_to_model(model, r)

activityactivity_assemble_default = activityactivity_assemble_one_step

if __name__ == '__main__':
    pa = PysbAssembler()
    bp = bel_api.process_belrdf('data/RAS_neighborhood.rdf')
    pa.add_statements(bp.statements)
    # bp = bel_api.process_ndex_neighborhood("ARAF")
    # pa.add_statements(bp.statements)
    # tp = trips_api.process_text("BRAF phosphorylates MEK1 at Ser222")
    # pa.add_statements(tp.statements)
    model = pa.make_model()
