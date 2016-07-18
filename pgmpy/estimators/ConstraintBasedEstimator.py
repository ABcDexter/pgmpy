#!/usr/bin/env python
import numpy as np
import pandas as pd
from warnings import warn
from itertools import combinations
from scipy.stats import chisquare

from pgmpy.base import UndirectedGraph
from pgmpy.models import BayesianModel
from pgmpy.estimators import StructureEstimator


class ConstraintBasedEstimator(StructureEstimator):
    def __init__(self, data, **kwargs):
        """
        Class for constraint-based estimation of BayesianModels from a given
        data set. Identifies (conditional) dependencies in data set using
        chi_square dependency test and uses the PC algorithm to estimate a DAG
        pattern that satisfies the identified dependencies. The DAG pattern can
        then be completed to a faithful BayesianModel, if possible.

        Parameters
        ----------
        data: pandas DataFrame object
            datafame object where each column represents one variable.
            (If some values in the data are missing the data cells should be set to `numpy.NaN`.
            Note that pandas converts each column containing `numpy.NaN`s to dtype `float`.)

        state_names: dict (optional)
            A dict indicating, for each variable, the discrete set of states (or values)
            that the variable can take. If unspecified, the observed values in the data set
            are taken to be the only possible states.

        complete_samples_only: bool (optional, default `True`)
            Specifies how to deal with missing data, if present. If set to `True` all rows
            that contain `np.Nan` somewhere are ignored. If `False` then, for each variable,
            every row where neither the variable nor its parents are `np.NaN` is used.
            This sets the behavior of the `state_count`-method.

        References
        ----------
        [1] Koller & Friedman, Probabilistic Graphical Models - Principles and Techniques,
            2009, Section 18.2
        [2] Neapolitan, Learning Bayesian Networks, Section 10.1.2 for the PC algorithm (page 550),
        http://www.cs.technion.ac.il/~dang/books/Learning%20Bayesian%20Networks(Neapolitan,%20Richard).pdf
        """
        super(ConstraintBasedEstimator, self).__init__(data, **kwargs)

    def estimate(self, p_value=0.05):
        """Estimates a DAG pattern (DirectedGraph) based on identified independencies
        from the data set, using the PC algorithm. Independencies are determined
        using a chi-squared statistic with the acceptance threshold of `p_value`.

        Parameters
        ----------
        p_value: float, default: 0.05
            A significance level to use for conditional independence tests in
            the data set. The p_value is the threshold probability of falsely
            rejecting the hypothesis that variables are conditionally dependent.

            The lower `p_value`, the more likely we are to reject dependencies,
            resulting in a sparser graph.

        Returns
        -------
        pdag: DirectedGraph
            An estimate for the DAG pattern of the BN underlying the data. The
            graph might contain some nodes with both-way edges (X->Y and Y->X).
            Any completion by (removing one of the both-way edges for each such
            pair) results in a I-equivalent Bayesian network DAG.

        Reference
        ---------
        Neapolitan, Learning Bayesian Networks, Section 10.1.2, Algorithm 10.2 (page 550)
        http://www.cs.technion.ac.il/~dang/books/Learning%20Bayesian%20Networks(Neapolitan,%20Richard).pdf


        Examples
        --------
        >>> import pandas as pd
        >>> import numpy as np
        >>> from pgmpy.base import DirectedGraph
        >>> from pgmpy.estimators import ConstraintBasedEstimator
        >>> data = pd.DataFrame(np.random.randint(0, 4, size=(5000, 3)), columns=list('ABD'))
        >>> data['C'] = data['A'] - data['B']
        >>> data['D'] += data['A']
        >>> c = ConstraintBasedEstimator(data)
        >>> pdag = c.estimate()
        >>> pdag.edges() # edges: A->C, B->C, A--D (not directed)
        [('B', 'C'), ('A', 'C'), ('A', 'D'), ('D', 'A')]
        """

        skel, seperating_sets = self.estimate_skeleton(p_value)
        pdag = skel.to_directed()
        node_pairs = combinations(pdag.nodes(), 2)

        # 1) for each X-Z-Y, if Z not in the seperating set of X,Y, then orient edges as X->Z<-Y
        # (Algorithm 3.4 in Koller & Friedman PGM, page 86)
        for X, Y in node_pairs:
            if not skel.has_edge(X, Y):
                for Z in set(skel.neighbors(X)) & set(skel.neighbors(X)):
                    if Z not in seperating_sets[frozenset((X, Y))]:
                        pdag.remove_edges_from([(Z, X), (Z, Y)])

        progress = True
        while progress:  # as long as edges can be oriented (removed)
            num_edges = pdag.number_of_edges()

            # 2) for each X->Z-Y, orient edges to Z->Y
            for X, Y in node_pairs:
                for Z in ((set(pdag.successors(X)) - set(pdag.predecessors(X))) &
                          (set(pdag.successors(Y)) & set(pdag.predecessors(Y)))):
                    pdag.remove(Y, Z)

            # 3) for each X-Y with a directed path from X to Y, orient edges to X->Y
            for X, Y in node_pairs:
                for path in nx.all_simple_paths(pdag, X, Y):
                    is_directed = True
                    for src, dst in path:
                        if pdag.has_edge(dst, src):
                            is_directed = False
                    if is_directed:
                        pdag.remove(Y, X)
                        break

            # 4) for each X-Z-Y with X->W, Y->W, and Z-W, orient edges to Z->W
            for X, Y in node_pairs:
                for Z in (set(pdag.successors(X)) & set(pdag.predecessors(X)) &
                          set(pdag.successors(Y)) & set(pdag.predecessors(Y))):
                    for W in ((set(pdag.successors(X)) - set(pdag.predecessors(X))) &
                              (set(pdag.successors(Y)) - set(pdag.predecessors(Y))) &
                              (set(pdag.successors(Z)) & set(pdag.predecessors(Z)))):
                        pdag.remove(W, Z)

            progress = num_edges > pdag.number_of_edges()

        return pdag

    def estimate_skeleton(self, p_value=0.05):
        """Estimates a graph skeleton (UndirectedGraph) for the data set, using
        the first part of the PC algorithm. Independencies are determined using
        a chisquare statistic with the acceptance threshold of `p_value`.
        Returns a tuple `(skeleton, seperating_sets).

        Parameters
        ----------
        p_value: float, default: 0.05
            A significance level to use for conditional independence tests in
            the data set. The p_value is the threshold probability of falsely
            rejecting the hypothesis that variables are conditionally dependent.

            The lower `p_value`, the more likely we are to reject dependencies,
            resulting in a sparser graph.

        Returns
        -------
        skeleton: UndirectedGraph
            An estimate for the undirected graph skeleton of the BN underlying the data.
        seperating_sets: dict
            A dict containing for each pair of not directly connected nodes a
            seperating set of variables that makes then conditionally independent.
            (needed for edge orientation procedures)

        Reference
        ---------
        [1] Neapolitan, Learning Bayesian Networks, Section 10.1.2, Algorithm 10.2 (page 550)
            http://www.cs.technion.ac.il/~dang/books/Learning%20Bayesian%20Networks(Neapolitan,%20Richard).pdf
        [1] Koller & Friedman, Probabilistic Graphical Models - Principles and Techniques, 2009
            Section 3.4.2.1 (page 85), Algorithm 3.3

        Examples
        --------
        >>> import pandas as pd
        >>> import numpy as np
        >>> from pgmpy.estimators import ConstraintBasedEstimator
        >>>
        >>> data = pd.DataFrame(np.random.randint(0, 2, size=(5000, 5)), columns=list('ABCDE'))
        >>> data['F'] = data['A'] + data['B'] + data ['C']
        >>> est = ConstraintBasedEstimator(data)
        >>> skel, sep_sets = est.estimate_skeleton()
        >>> skel.edges()
        [('A', 'F'), ('B', 'F'), ('C', 'F')]
        >>> # all independencies are unconditional:
        >>> sep_sets
        {('D', 'A'): (), ('C', 'A'): (), ('C', 'E'): (), ('E', 'F'): (), ('B', 'D'): (),
         ('B', 'E'): (), ('D', 'F'): (), ('D', 'E'): (), ('A', 'E'): (), ('B', 'A'): (),
         ('B', 'C'): (), ('C', 'D'): ()}
        >>>
        >>> data = pd.DataFrame(np.random.randint(0, 2, size=(5000, 3)), columns=list('XYZ'))
        >>> data['X'] += data['Z']
        >>> data['Y'] += data['Z']
        >>> est = ConstraintBasedEstimator(data)
        >>> skel, sep_sets = est.estimate_skeleton()
        >>> skel.edges()
        [('X', 'Z'), ('Y', 'Z')]
        >>> # X, Y dependent, but conditionally independent given Z:
        >>> sep_sets
        {('X', 'Y'): ('Z',)}
        >>>
        """

        nodes = self.state_names.keys()
        graph = UndirectedGraph(combinations(nodes, 2))
        lim_neighbors = 0
        seperating_sets = dict()
        while not all([len(graph.neighbors(node)) < lim_neighbors for node in nodes]):
            for node in nodes:
                for neighbor in graph.neighbors(node):
                    # search if there is a set of neighbors (of size lim_neighbors)
                    # that makes X and Y independent:
                    for seperating_set in combinations(set(graph.neighbors(node)) - set([neighbor]), lim_neighbors):
                        if self.test_conditional_independence(node, neighbor, seperating_set) >= p_value:
                            # reject hypothesis that they are dependent
                            seperating_sets[frozenset((node, neighbor))] = seperating_set
                            graph.remove_edge(node, neighbor)
                            break
            lim_neighbors += 1

        return graph, seperating_sets

    def test_conditional_independence(self, X, Y, Zs=[]):
        """Chi-square conditional independence test.
        Tests if X is independent from Y given Zs in the data.

        This is done by comparing the observed frequencies with the expected
        frequencies if X,Y were conditionally independent, using a chisquare
        deviance statistic. The expected frequencies given independence are
        `P(X,Y,Zs) = P(X|Zs)*P(Y|Zs)*P(Zs)`. The latter term can be computed
        as `P(X,Zs)*P(Y,Zs)/P(Zs).

        Parameters
        ----------
        X: int, string, hashable object
            A variable name contained in the data set
        Y: int, string, hashable object
            A variable name contained in the data set, different from X
        Zs: list of variable names
            A list of variable names contained in the data set, different from X and Y.
            This is the seperating set that (potentially) makes X and Y independent.
            Default: []

        Returns
        -------
        p_value: float
            A significance level for the hypothesis that X and Y are dependent
            given Zs. The p_value is the probability of falsely rejecting the
            hypothesis that the variables are conditionally dependent. A low
            p_value (e.g. below 0.05 or 0.01) indicates dependence. (The lower
            the threshold for the p_value, the more likely we are to reject
            dependency, resulting in a sparser graph.)

        References
        ----------
        [1] Koller & Friedman, Probabilistic Graphical Models - Principles and Techniques, 2009
        Section 18.2.2.3 (page 789)
        [2] Neapolitan, Learning Bayesian Networks, Section 10.3 (page 600ff)
            http://www.cs.technion.ac.il/~dang/books/Learning%20Bayesian%20Networks(Neapolitan,%20Richard).pdf
        [3] Chi-square test https://en.wikipedia.org/wiki/Pearson%27s_chi-squared_test#Test_of_independence

        Examples
        --------
        >>> import pandas as pd
        >>> import numpy as np
        >>> from pgmpy.estimators import ConstraintBasedEstimator
        >>> data = pd.DataFrame(np.random.randint(0, 2, size=(50000, 4)), columns=list('ABCD'))
        >>> data['E'] = data['A'] + data['B'] + data['C']
        >>> c = ConstraintBasedEstimator(data)
        >>> print(c.test_conditional_independence('A', 'C'))  # independent
        0.9848481578
        >>> print(c.test_conditional_independence('A', 'B', 'D'))  # independent
        0.962206185665
        >>> print(c.test_conditional_independence('A', 'B', ['D', 'E']))  # dependent
        0.0
        """

        if isinstance(Zs, (frozenset, list, set, tuple,)):
            Zs = list(Zs)
        else:
            Zs = [Zs]

        # Check is sample size is sufficient. Require at least 5 samples per parameter (on average)
        # (As suggested in Spirtes et al., Causation, Prediction and Search, 2000, and also used in
        # Tsamardinos et al., The max-min hill-climbing BN structure learning algorithm, 2005, Section 4)
        num_params = ((len(self.state_names[X])-1) *
                      (len(self.state_names[Y])-1) *
                      np.prod([len(self.state_names[Z]) for Z in Zs]))
        if len(self.data) < num_params:
            warn("Insufficient data for testing {0} _|_ {1} | {2}. ".format(X, Y, Zs) +
                 "At least {0} samples recommended, {1} present.".format(num_params, len(self.data)))

        # compute actual frequency/state_count table:
        # = P(X,Y,Zs)
        XYZ_state_counts = pd.crosstab(index=self.data[X],
                                       columns=[self.data[Y]] + [self.data[Z] for Z in Zs])
        # reindex to add missing rows & columns (if some values don't appear in data)
        row_index = self.state_names[X]
        column_index = pd.MultiIndex.from_product(
                            [self.state_names[Y]] + [self.state_names[Z] for Z in Zs], names=[Y]+Zs)
        XYZ_state_counts = XYZ_state_counts.reindex(index=row_index,    columns=column_index).fillna(0)

        # compute the expected frequency/state_count table if X _|_ Y | Zs:
        # = P(X|Zs)*P(Y|Zs)*P(Zs) = P(X,Zs)*P(Y,Zs)/P(Zs)
        if Zs:
            XZ_state_counts = XYZ_state_counts.sum(axis=1, level=Zs)  # marginalize out Y
            YZ_state_counts = XYZ_state_counts.sum().unstack(Zs)      # marginalize out X
        else:
            XZ_state_counts = XYZ_state_counts.sum(axis=1)
            YZ_state_counts = XYZ_state_counts.sum()
        Z_state_counts = YZ_state_counts.sum()  # marginalize out both

        XYZ_expected = pd.DataFrame(index=XYZ_state_counts.index, columns=XYZ_state_counts.columns)
        for X_val in XYZ_expected.index:
            if Zs:
                for Y_val in XYZ_expected.columns.levels[0]:
                    XYZ_expected.loc[X_val, Y_val] = (XZ_state_counts.loc[X_val] *
                                                      YZ_state_counts.loc[Y_val] /
                                                      Z_state_counts).values
            else:
                for Y_val in XYZ_expected.columns:
                    XYZ_expected.loc[X_val, Y_val] = (XZ_state_counts.loc[X_val] *
                                                      YZ_state_counts.loc[Y_val] /
                                                      Z_state_counts)

        observed = XYZ_state_counts.values.flatten()
        expected = XYZ_expected.values.flatten()
        # remove elements where the expected value is 0;
        # this also corrects the degrees of freedom for chisquare
        observed, expected = zip(*((o, e) for o, e in zip(observed, expected) if not e == 0))

        chi2, p_value = chisquare(observed, expected)

        return p_value
