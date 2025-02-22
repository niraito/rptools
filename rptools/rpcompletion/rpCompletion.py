from csv import reader as csv_reader
from itertools import product as itertools_product
from io import StringIO
import pandas as pd
from typing import (
    List,
    Dict,
    Tuple
)
from logging import (
    Logger,
    getLogger
)
from copy import deepcopy
from brs_utils import (
    insert_and_or_replace_in_sorted_list,
    Item,
    Cache
)
from rr_cache import rrCache
from rxn_rebuild import rebuild_rxn
from rptools.rplibs import (
    rpPathway,
    rpReaction,
    rpCompound
)
from .Args import (
    default_upper_flux_bound,
    default_lower_flux_bound,
    default_max_subpaths_filter
)

def rp_completion(
    rp2_metnet,
    sink,
    rp2paths_compounds,
    rp2paths_pathways,
    cache: rrCache = None,
    upper_flux_bound: float = default_upper_flux_bound,
    lower_flux_bound: float = default_lower_flux_bound,
    max_subpaths_filter: int = default_max_subpaths_filter,
    logger: Logger = getLogger(__name__)
) -> List[rpPathway]:
    """Process to the completion of metabolic pathways 
    generated by RetroPath2.0 and rp2paths.

    (1) rp2paths generates a sets of master pathways which 
    each of them is a set of chemical transformations.

    (2) Each chemical transformation refers to one or 
    multiple reaction rule.

    (3) Each reaction rule comes from one or multiple 
    template (original) chemical reaction

    The completion consists in:

    1. exploring all possible metabolic pathways through 
    steps (2) and (3)

    2. putting back chemical species removed during reaction 
    rules building process
    
    The completion is done for all master pathways of step (1).

    Parameters
    ----------
    rp2_metnet: str
        Path to the file containing the metabolic network
    sink: str
        Path to the file containing the list of
        species in the sink
    rp2paths_compounds: str
        Path to the file containing the chemical
        species involved in master metabolic pathways
    rp2paths_pathways: str
        Path to the file containing the master metabolic
        pathways
    cache: rrCache, optional
        Cache that contains reaction rules data
    upper_flux_bound: float, optional
        Upper flux bound for all new reactions created
        (default: default_upper_flux_bound from Args file),
    lower_flux_bound: float, optional
        Lower flux bound for all new reactions created
        (default: default_lower_flux_bound from Args file),
    max_subpaths_filter: int, optional
        Number of pathways (best) kept per master pathway
        (default: 10)
    logger: Logger, optional

    Returns
    -------
    List of rpPathway objects
    """

    if cache is None:
        cache = rrCache(
            attrs=[
                'rr_reactions',
                'template_reactions',
                'cid_strc',
                'deprecatedCompID_compid',
            ]
        )

    ## READ
    __rp2paths_compounds_in_cache(
        infile=rp2paths_compounds,
        cache=cache,
        logger=logger
    )
    pathways, transfos = __read_pathways(
        infile=rp2paths_pathways,
        logger=logger
    )
    ec_numbers = __read_rp2_metnet(
        infile=rp2_metnet,
        logger=logger
    )
    sink_molecules = __read_sink(
        infile=sink,
        logger=logger
    )

    # COMPLETE TRANSFORMATIONS
    full_transfos = __complete_transformations(
        transfos=transfos,
        ec_numbers=ec_numbers,
        cache=cache,
        logger=logger
    )

    # GENERATE THE COMBINATORY OF SUB-PATHWAYS
    # Build pathways over:
    #   - multiple reaction rules per transformation (TRS) and
    #   - multiple template reactions per reaction rule
    pathway_combinatorics = __build_pathway_combinatorics(
        full_transfos,
        pathways,
        cache=cache,
        logger=logger
    )

    # BUILD + RANK SUB-PATHWAYS 
    all_pathways = __build_all_pathways(
        pathways=pathway_combinatorics,
        transfos=full_transfos,
        sink_molecules=sink_molecules,
        rr_reactions=cache.get('rr_reactions'),
        compounds_cache=cache.get('cid_strc'),
        max_subpaths_filter=max_subpaths_filter,
        lower_flux_bound=lower_flux_bound,
        upper_flux_bound=upper_flux_bound,
        logger=logger
    )

    return all_pathways


def __complete_transformations(
    transfos: Dict,
    ec_numbers: Dict,
    cache: rrCache,
    logger: Logger = getLogger(__name__)
) -> Dict:
    """From template reactions, put back chemical species
    that have been removed during the reaction rules
    building process.
    Template reactions are stored in the cache.

    Parameters
    ----------
    transfos: Dict
        Stoichiometric chemical transformations to complete
    ec_numbers: Dict
        EC numbers of each chemical transformation (stored
        as inforamtion)
    cache: rrCache
        Cache that contains reaction rules data
    logger: Logger, optional

    Returns
    -------
    Completed stoichiometric chemical transformations
    """

    logger.debug(f'transfos: {transfos}')
    logger.debug(f'ec_numbers: {ec_numbers}')

    full_transfos = {}

    # For each transformation
    for transfo_id, transfo in transfos.items():

        full_transfos[transfo_id] = {}
        full_transfos[transfo_id]['ec'] = ec_numbers[transfo_id]['ec']
        # Convert transformation into SMILES
        transfo_smi = '{left}>>{right}'.format(
                left=__build_smiles(transfo['left']),
                right=__build_smiles(transfo['right'])
            )

        # Add compounds of the current transformation
        full_transfos[transfo_id]['left'] = dict(transfos[transfo_id]['left'])
        full_transfos[transfo_id]['right'] = dict(transfos[transfo_id]['right'])
        full_transfos[transfo_id]['complement'] = {}

        # MULTIPLE RR FOR ONE TRANSFO
        for rule_id in transfo['rule_ids']:

            # MULTIPLE TEMPLATE REACTIONS FOR ONE RR
            # If 'tmpl_rxn_id' is not given,
            # the transformation will be completed
            # for each template reaction from reaction rule was built from
            full_transfos[transfo_id]['complement'][rule_id] = rebuild_rxn(
                cache=cache,
                rxn_rule_id=rule_id,
                transfo=transfo_smi,
                direction='forward',
                # tmpl_rxn_id=tmpl_rxn_id,
                logger=logger
            )

    return full_transfos


def __build_smiles(
    side: Dict,
    logger: Logger = getLogger(__name__)
) -> str:
    """Build SMILES string from a
    stoichiometric chemical reaction side

    Parameters
    ----------
    side: Dict
        Stoichiometric chemical reaction side
    logger: Logger, optional

    Returns
    -------
    SMILES string
    """
    return '.'.join([Cache.get(spe_id).get_smiles() for spe_id in side.keys()])

def __build_reader(
    path: str,
    delimiter: str = ',',
    logger: Logger=getLogger(__name__)
) -> 'csv_reader':
    """Build a CSV reader object

    Parameters
    ----------
    path: str
        Path to the file to build the reader from
    delimiter: str, optional
        Pattern to separate columns
    logger: Logger, optional

    Returns
    -------
    CSV reader object
    """
    if isinstance(path, bytes):
        reader = csv_reader(
            StringIO(path.decode('utf-8')),
            delimiter=delimiter
        )
    else:
        try:
            reader = csv_reader(
                open(path, 'r'),
                delimiter=delimiter
            )
        except FileNotFoundError:
            logger.error('Could not read file: '+str(path))
            return None
    next(reader)
    return reader


def __rp2paths_compounds_in_cache(
    infile: str,
    cache: rrCache,
    logger: Logger = getLogger(__name__)
) -> None:
    """Add compounds involved in metabolic pathways
    to the cache

    Parameters
    ----------
    infile: str
        Path to file to read compounds data from
    cache: rrCache
        Reaction rules cache data
    logger: Logger, optional
    """

    try:
        reader = __build_reader(
            path=infile,
            delimiter='\t',
            logger=logger
        )
        if reader is None:
            logger.error(f'File not found: {infile}')
            return None
        for row in reader:
            spe_id = row[0]
            smiles = row[1]
            cmpd = __get_compound_from_cache(
                spe_id=spe_id,
                smiles=smiles,
                cache=cache,
                logger=logger
            )
            # Create the compound that will add it to the cache
            rpCompound(
                id=spe_id,
                smiles=smiles,
                inchi=cmpd['inchi'],
                inchikey=cmpd['inchikey'],
                name=cmpd['name'],
                formula=cmpd['formula']
            )

    except TypeError as e:
        logger.error('Could not read the compounds file ('+str(infile)+')')
        raise RuntimeError


def __get_compound_from_cache(
    spe_id: str,
    smiles: str,
    cache: rrCache,
    logger: Logger = getLogger(__name__)
) -> Dict[str, str]:
    """Get compound data from cache

    Parameters
    ----------
    spe_id: str
        ID of the chemical species to get data
    smiles: str
        SMILES string of the chemical species
        to get data
    cache: rrCache
        Reaction Rules cache
    logger: Logger, optional

    Returns
    -------
    Dictionary with chemical species
    informations ('inchi', 'inchikey',
    'name', 'formula')
    """
    try:
        inchi = cache.get('cid_strc')[spe_id]['inchi']
    except KeyError:
        # try to generate them yourself by converting them directly
        try:
            resConv = cache._convert_depiction(
                idepic=smiles,
                itype='smiles',
                otype={'inchi'}
            )
            inchi = resConv['inchi']
        except NotImplementedError as e:
            logger.warning('Could not convert the following SMILES to InChI: '+str(smiles))
    try:
        inchikey = cache.get('cid_strc')[spe_id]['inchikey']
    # try to generate them yourself by converting them directly
    # TODO: consider using the inchi writing instead of the SMILES notation to find the inchikey
    except KeyError:
        try:
            resConv = cache._convert_depiction(
                idepic=smiles,
                itype='smiles',
                otype={'inchikey'}
            )
            inchikey = resConv['inchikey']
        except NotImplementedError as e:
            logger.warning('Could not convert the following SMILES to InChI key: '+str(smiles))
    try:
        name = cache.get('cid_strc')[spe_id]['name']
    except KeyError:
        name = ''
    try:
        formula = cache.get('cid_strc')[spe_id]['formula']
    except KeyError:
        formula = ''

    return {
        'inchi': inchi,
        'inchikey': inchikey,
        'name': name,
        'formula': formula
    }

def __read_rp2_metnet(
    infile: str,
    logger: Logger = getLogger(__name__)
) -> Dict:
    """Read EC numbers of chemical transformations
    from RetroPath2.0 metabolic network

    Parameters
    ----------
    infile: str
        Path to metabolic network file
    logger: Logger, optional

    Returns
    -------
    EC numbers of chemical reactions involved
    in the metabolic network
    """

    ec_numbers = {}
    reader = __build_reader(path=infile, logger=logger)
    if reader is None:
        logger.error(f'File not found: {infile}')
        return {}
    for row in reader:
        if row[1] not in ec_numbers:
            ec_numbers[row[1]] = {
                'ec': [i.replace(' ', '') for i in row[11][1:-1].split(',') if i.replace(' ', '')!='NOEC'],
            }

    logger.debug(ec_numbers)

    return ec_numbers


def __read_sink(
    infile: str,
    logger: Logger = getLogger(__name__)
) -> List[str]:
    """Reads chemical species that are in the sink

    Parameters
    ----------
    infile: str
        Path to the file containing the sink
    logger: Logger, optional

    Returns
    -------
    List of chemical species ID that are in the sink
    """

    sink_molecules = set()
    reader = __build_reader(path=infile, logger=logger)
    if reader is None:
        logger.error(f'File not found: {infile}')
        return []
    for row in reader:
        sink_molecules.add(row[0])

    logger.debug(list(sink_molecules))

    return list(sink_molecules)


def __read_pathways(
    infile: str,
    logger:  Logger = getLogger(__name__)
) -> Tuple[Dict, Dict]:
    """Reads metabolic pathways and
    chemical reactions from a file

    Parameters
    ----------
    infile: str
        Path to the file to read data from
    logger: Logger, oprional

    Returns
    -------
    Metabolic pathways and chemical transformations
    as dictionnaries
    """

    df = pd.read_csv(infile)

    check = __check_pathways(df)
    if not check:
        logger.error(check)
        exit()

    pathways = {}
    transfos = {}

    for index, row in df.iterrows():

        path_id = row['Path ID']
        transfo_id = row['Unique ID'][:-2]

        if path_id in pathways:
            pathways[path_id] += [transfo_id]
        else:
            pathways[path_id] = [transfo_id]

        if transfo_id not in transfos:
            transfos[transfo_id] = {}
            transfos[transfo_id]['rule_ids'] = row['Rule ID'].split(',')
            for side in ['left', 'right']:
                transfos[transfo_id][side] = {}
                # split compounds
                compounds = row[side[0].upper()+side[1:]].split(':')
                # read compound and its stochio 
                for compound in compounds:
                    sto, spe = compound.split('.')
                    transfos[transfo_id][side][spe] = int(sto)

    return pathways, transfos


def __check_pathways(df) -> bool:
    """Checks pathways data as pandas dataframe

    Parameters
    ----------
    df: pandas.DataFrame
        Pathways as pandas dataframe

    Returns
    -------
    True if data are ok, False otherwise
    """
    if len(df) == 0:
        return 'infile is empty'

    if df['Path ID'].dtypes != 'int64':
        return '\'Path ID\' column contain non integer value(s)'

    return True


def __build_pathway_combinatorics(
    full_transfos: Dict,
    pathways: Dict,
    cache: rrCache,
    logger: Logger = getLogger(__name__)
) -> Dict:
    '''Build all combinations of sub-pathways based on these facts:
          - one single transformation can have been formed from multiple reaction rules, and
          - one single reaction rule can have been generated from multiple template reactions
       To each such a combination corresponds a different complete transformation, i.e. reaction,
       then a different sub-pathway.

    Parameters
    ----------
    full_transfos : Dict
        Set of completed transformations
    pathways : Dict
        Set of pathways (pathway: list of transformation IDs)
    logger : Logger

    Returns
    -------
    Set of sub-pathways. Each transfomation IDs in 'pathways'
    has been replaced by a list of triplet(transfo_id, rule_id, tmpl_rxn_id)
    '''

    # BUILD PATHWAYS WITH ALL REACTIONS (OVER REACTION RULES * TEMPLATE REACTIONS)
    pathways_all_reactions = {}

    ## ITERATE OVER PATHWAYS
    for pathway, transfos_lst in pathways.items():

        # index of chemical reaction within the pathway
        transfo_idx = 0

        ## ITERATE OVER TRANSFORMATIONS
        # For each transformation of the current pathway
        # Iterate in retrosynthesis order (reverse)
        #   to better combine all sub-pathways
        for transfo_id in transfos_lst:

            transfo_idx += 1
            # Compounds from original transformation
            compounds = {
                'right': dict(full_transfos[transfo_id]['right']),
                'left': dict(full_transfos[transfo_id]['left'])
            }
            # Build list of transformations
            # where each transfo can correspond to multiple reactions
            # due to multiple reaction rules and/or multiple template reactions
            if pathway not in pathways_all_reactions:
                pathways_all_reactions[pathway] = []

            ## ITERATE OVER REACTION RULES
            # Append a list to add steps later on
            # ('pathways_all_reactions[pathway][-1].append(...')
            pathways_all_reactions[pathway].append([])
            # Multiple reaction rules for the current transformation?
            for rule_ids, tmpl_rxns in full_transfos[transfo_id]['complement'].items():

                ## ITERATE OVER TEMPLATE REACTIONS
                # Current reaction rule generated from multiple template reactions?
                for tmpl_rxn_ids, tmpl_rxn in tmpl_rxns.items():

                    # Add template reaction compounds
                    compounds = __add_compounds(
                        compounds,
                        tmpl_rxn['added_cmpds']
                    )

                    # Add the triplet ID to identify the sub_pathway
                    pathways_all_reactions[pathway][-1].append(
                        {
                            'rp2_transfo_id': transfo_id,
                            'rule_ids': rule_ids,
                            'tmpl_rxn_ids': tmpl_rxn_ids
                        }
                    )

    return pathways_all_reactions


def __build_all_pathways(
    pathways: Dict,
    transfos: Dict,
    sink_molecules: List,
    rr_reactions: Dict,
    compounds_cache: Dict,
    max_subpaths_filter: int,
    lower_flux_bound: float,
    upper_flux_bound: float,
    logger: Logger = getLogger(__name__)
) -> Dict:
    """Builds pathways based on all combinations over
    reaction rules and template reactions (see
    `build_pathway_combinatorics` documentation).

    Parameters
    ----------
    pathways: Dict
        Metabolic pathways as list of chemical
        reactions where each reaction is defined by:
            - transformation ID,
            - reaction rule ID, and
            - template reaction ID
    transfos: Dict
        Full chemical transformations
    sink_molecules: List
        Sink chemical species IDs
    rr_reactions: Dict
        Reaction rules cache
    compounds_cache: Dict
        Compounds cache
    max_subpaths_filter: int
        Number of pathways (best) kept per master pathway
    lower_flux_bound: float
        Lower flux bound for all new reactions created
    upper_flux_bound: float
        Upper flux bound for all new reactions created
    logger: Logger, optional

    Returns
    -------
    Set of ranked rpPathway objects
    """

    res_pathways = {}

    nb_pathways = 0
    nb_unique_pathways = 0

    ## PATHWAYS
    for path_idx, transfos_lst in pathways.items():

        # Combine over multiple template reactions
        sub_pathways = list(itertools_product(*transfos_lst))

        ## SUB-PATHWAYS
        # # Keep only topX best sub_pathways
        # # within a same master pathway
        res_pathways[path_idx] = []
        for sub_path_idx in range(len(sub_pathways)):

            pathway = rpPathway(
                id=str(path_idx).zfill(3)+'_'+str(sub_path_idx+1).zfill(4),
                logger=logger
            )
            logger.debug(pathway.get_id())

            ## ITERATE OVER REACTIONS
            nb_reactions = len(sub_pathways[sub_path_idx])
            for rxn_idx in range(nb_reactions):

                rxn = sub_pathways[sub_path_idx][rxn_idx]
                transfo_id = rxn['rp2_transfo_id']
                transfo = transfos[transfo_id]
                rule_ids = rxn['rule_ids']
                tmpl_rxn_id = rxn['tmpl_rxn_ids']

                ## COMPOUNDS
                # Template reaction compounds
                added_cmpds = transfo['complement'][rule_ids][tmpl_rxn_id]['added_cmpds']
                # Add missing compounds to the cache
                for side in added_cmpds.keys():
                    for spe_id in added_cmpds[side].keys():
                        logger.debug(f'Add missing compound {spe_id}')
                        if spe_id not in Cache.get_objects():
                            try:
                                rpCompound(
                                    id=spe_id,
                                    smiles=compounds_cache[spe_id]['smiles'],
                                    inchi=compounds_cache[spe_id]['inchi'],
                                    inchikey=compounds_cache[spe_id]['inchikey'],
                                    formula=compounds_cache[spe_id]['formula'],
                                    name=compounds_cache[spe_id]['name']
                                )
                            except KeyError:
                                rpCompound(
                                    id=spe_id
                                )

                ## REACTION
                # Compounds from original transformation
                core_species = {
                    'right': deepcopy(transfo['right']),
                    'left': deepcopy(transfo['left'])
                }
                compounds = __add_compounds(core_species, added_cmpds)
                # revert reaction index (forward)
                rxn_idx_forward = nb_reactions - rxn_idx
                rxn = rpReaction(
                    id='rxn_'+str(rxn_idx_forward),
                    ec_numbers=transfo['ec'],
                    reactants=dict(compounds['left']),
                    products=dict(compounds['right']),
                    lower_flux_bound=lower_flux_bound,
                    upper_flux_bound=upper_flux_bound
                )
                # write infos
                for info_id, info in sub_pathways[sub_path_idx][rxn_idx].items():
                    getattr(rxn, 'set_'+info_id)(info)
                rxn.set_rule_score(rr_reactions[rule_ids][tmpl_rxn_id]['rule_score'])
                rxn.set_idx_in_path(rxn_idx_forward)

                # Add at the beginning of the pathway
                # to have the pathway in forward direction
                # Search for the target in the current reaction
                target_id = [spe_id for spe_id in rxn.get_products_ids() if 'TARGET' in spe_id]
                if target_id != []:
                    target_id = target_id[0]
                else:
                    target_id = None
                logger.debug(f'rxn: {rxn._to_dict()}')
                pathway.add_reaction(
                    rxn=rxn,
                    target_id=target_id
                )

                ## TRUNK SPECIES
                pathway.add_species_group(
                    'trunk',
                    [
                        spe_id
                        for value
                        in core_species.values()
                        for spe_id in value.keys()
                    ]
                )

                ## COMPLETED SPECIES
                pathway.add_species_group(
                    'completed',
                    [
                        spe_id
                        for value
                        in added_cmpds.values()
                        for spe_id in value.keys()
                    ]
                )

            ## SINK
            pathway.set_sink_species(
                list(
                    set(pathway.get_species_ids()) & set(sink_molecules)
                )
            )

            nb_pathways += 1

            ## RANK AMONG ALL SUB-PATHWAYS OF THE CURRENT MASTER PATHWAY
            res_pathways[path_idx] = __keep_unique_pathways(
                res_pathways[path_idx],
                pathway,
                logger
            )

        nb_unique_pathways += len(res_pathways[path_idx])

    # Flatten lists of pathways
    pathways = sum(
        [
            pathways
            for pathways in res_pathways.values()
        ], [])

    # Globally sort pathways
    pathways = sorted(pathways)[-max_subpaths_filter:]

    logger.info(f'Pathways statistics')
    logger.info(f'-------------------')
    logger.info(f'   pathways: {nb_pathways}')
    logger.info(f'   unique pathways: {nb_unique_pathways}')
    logger.info(f'   selected pathways: {len(pathways)} (topX filter = {max_subpaths_filter})')

    # Return topX pathway objects
    return [
        pathway.object
        for pathway in pathways
    ]

    # Transform the list of Item into a list of Pathway
    results = {}
    nb_sel_pathways = 0
    for res_pathway_idx, res_pathway in res_pathways.items():
        results[res_pathway_idx] = [pathway.object for pathway in res_pathway]
        nb_sel_pathways += len(results[res_pathway_idx])

    logger.info(f'Pathways selected: {nb_sel_pathways}/{nb_pathways}')

    return results


def __add_compounds(
    compounds: Dict,
    compounds_to_add: Dict,
    logger: Logger = getLogger(__name__)
) -> Dict:
    """Add stoichiometric chemical compounds
    to existing ones according to known structure
    or not.

    Parameters
    ----------
    compounds: Dict
        Existing stoichiometric chemical compounds
    compounds_to_add: Dict
        Stoichiometric chemical compounds to add
    logger: Logger, optional

    Returns
    -------
    Merge of the two sets of compounds by differentiating
    if compounds have known structure or not.
    """
    _compounds = deepcopy(compounds)
    for side in ['right', 'left']:
        # added compounds with struct
        for cmpd_id, cmpd in compounds_to_add[side].items():
            if cmpd_id in _compounds[side]:
                _compounds[side][cmpd_id] += cmpd['stoichio']
            else:
                _compounds[side][cmpd_id] = cmpd['stoichio']
        # added compounds with no struct
        for cmpd_id, cmpd in compounds_to_add[side+'_nostruct'].items():
            if cmpd_id in _compounds[side]:
                _compounds[side][cmpd_id] += cmpd['stoichio']
            else:
                _compounds[side][cmpd_id] = cmpd['stoichio']
    return _compounds

def __keep_unique_pathways(
    pathways: List[Dict],
    pathway: rpPathway,
    logger: Logger = getLogger(__name__)
) -> List[Dict]:
    '''
    Given a pathway object, looks if an equivalent pathway (cf rpSBML::__eq__ method)
    is present in the given list. If found, then compare scores and keep the highest.
    Otherwise, insert it in the list.

    Parameters
    ----------
    pathways: List[Dict]
        List of pathways sorted by increasing scores
    pathway: Dict
        Pathway to insert
    logger : Logger, optional
        The logger object.

    Returns
    -------
    best_pathways: List[Dict]
        List of pathways with highest scores
    '''

    # logger.debug('Best pathways:       ' + str([item for item in pathways]))
    # logger.debug('max_subpaths_filter: ' + str(max_subpaths_filter))
    # logger.debug('pathway:             ' + str(pathway))

    # from bisect import insort as bisect_insort
    # bisect_insort(best_rpsbml, sbml_item)

    # Detect if the predicted pathway is not already
    # in the list. If it is, then only add the template
    # reaction id in the list of the duplicated reaction(s)
    pathway_found = False
    for _pathway in pathways:
        if pathway == _pathway.object:
            pathway_found = True
            logger.debug(f'Equality between {_pathway.object.get_id()} and {pathway.get_id()}')
            logger.debug(pathway)
            logger.debug(_pathway.object)
            for rxn in pathway.get_list_of_reactions():
                for _rxn in _pathway.object.get_list_of_reactions():
                    if rxn == _rxn:
                        # Copy template reaction IDs from a reaction to another
                        for tmpl_rxn_id in rxn.get_tmpl_rxn_ids():
                            if tmpl_rxn_id not in _rxn.get_tmpl_rxn_ids():
                                _rxn.add_tmpl_rxn_id(tmpl_rxn_id)
                        # Copy reaction rule IDs from a reaction to another
                        for rule_id in rxn.get_rule_ids():
                            if rule_id not in _rxn.get_rule_ids():
                                _rxn.add_rule_id(rule_id)

    if not pathway_found:
        score = pathway.get_mean_rule_score()
        # Insert pathway in best_pathways list by increasing score
        pathways = insert_and_or_replace_in_sorted_list(
            Item(pathway, score),
            pathways
        )

    return pathways
