#  ___________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright 2017 National Technology and Engineering Solutions of Sandia, LLC
#  Under the terms of Contract DE-NA0003525 with National Technology and 
#  Engineering Solutions of Sandia, LLC, the U.S. Government retains certain 
#  rights in this software.
#  This software is distributed under the 3-clause BSD License.
#  ___________________________________________________________________________

from pyomo.core.kernel.numvalue import NumericValue
from pyomo.core.kernel.component_interface import \
    (IComponent,
     _ActiveComponentMixin,
     _ActiveComponentContainerMixin,
     _abstract_readwrite_property,
     _abstract_readonly_property)
from pyomo.core.kernel.component_dict import ComponentDict
from pyomo.core.kernel.component_tuple import ComponentTuple
from pyomo.core.kernel.component_list import ComponentList
from pyomo.core.kernel.component_constraint import (IConstraint,
                                                    constraint_tuple)
from pyomo.core.kernel.numvalue import value

import six
from six.moves import zip, xrange

try:
    import numpy
    has_numpy = True
except:     #pragma:nocover
    has_numpy = False

try:
    import scipy
    has_scipy = True
except:     #pragma:nocover
    has_scipy = False

_noarg = object()

#
# Note: This class is experimental. The implementation may
#       change or it may go away.
#

class _MatrixConstraintData(IConstraint,
                            _ActiveComponentMixin):
    """
    A placeholder object for linear constraints in a
    matrix_constraint container. A user should not
    directly instantiate this class.
    """
    # To avoid a circular import, for the time being, this
    # property will be set externally
    _ctype = None
    _linear_canonical_form = True
    __slots__ = ("_parent",
                 "_active",
                 "_index",
                 "__weakref__")

    def __init__(self, index):
        assert index >= 0
        self._parent = None
        self._active = True
        self._index = index

    @property
    def index(self):
        """The row index of this constraint in the parent matrix"""
        return self._index

    @property
    def terms(self):
        """An iterator over the terms in the body of this
        constraint as (variable, coefficient) tuples"""
        parent = self.parent
        variable_order = parent.variable_order
        if variable_order is None:
            raise ValueError(
                "No variable order has been assigned")
        A = parent._A
        if parent._sparse:
            for k in xrange(A.indptr[self._index],
                            A.indptr[self._index+1]):
                yield variable_order[A.indices[k]], A.data[k]
        else:
            for item in zip(variable_order,
                            A[self._index,:].tolist()):
                yield item

    #
    # Override a the default __call__ method on IConstraint
    # to avoid building the body expression
    #

    def __call__(self, exception=True):
        # don't mask an exception in the terms
        # property method
        if self.parent.variable_order is None:
            raise ValueError(
                "No variable order has been assigned")
        try:
            return sum(c*v() for v,c in self.terms)
        except (ValueError, TypeError):
            if exception:
                raise
            return None

    #
    # Define the IConstraint abstract methods
    #

    @property
    def body(self):
        """The body of the constraint"""
        return sum(c * v for v, c in self.terms)

    @property
    def lb(self):
        """The lower bound of the constraint"""
        return self.parent.lb[self._index]
    @lb.setter
    def lb(self, lb):
        if self.equality:
            raise ValueError(
                "The lb property can not be set "
                "when the equality property is True.")
        if lb is None:
            lb = -numpy.inf
        elif isinstance(lb, NumericValue):
            raise ValueError("lb must be set to "
                             "a simple numeric type "
                             "or None")
        self.parent.lb[self._index] = lb

    @property
    def ub(self):
        """The upper bound of the constraint"""
        return self.parent.ub[self._index]
    @ub.setter
    def ub(self, ub):
        if self.equality:
            raise ValueError(
                "The ub property can not be set "
                "when the equality property is True.")
        if ub is None:
            ub = numpy.inf
        elif isinstance(ub, NumericValue):
            raise ValueError("ub must be set to "
                             "a simple numeric type "
                             "or None")
        self.parent.ub[self._index] = ub

    @property
    def rhs(self):
        """The right-hand side of the constraint. This
        property can only be read when the equality property
        is :const:`True`. Assigning to this property
        implicitly sets the equality property to
        :const:`True`."""
        if not self.equality:
            raise ValueError(
                "The rhs property can not be read "
                "when the equality property is False.")
        return self.parent.lb[self._index]
    @rhs.setter
    def rhs(self, rhs):
        if rhs is None:
            # None has a different meaning depending on the
            # context (lb or ub), so there is no way to
            # interpret this
            raise ValueError(
                "Constraint right-hand side can not "
                "be assigned a value of None.")
        elif isinstance(rhs, NumericValue):
            raise ValueError("rhs must be set to "
                             "a simple numeric type "
                             "or None")
        self.parent.lb[self._index] = rhs
        self.parent.ub[self._index] = rhs
        self.parent.equality[self._index] = True

    @property
    def bounds(self):
        """The bounds of the constraint as a tuple (lb, ub)"""
        return (self.parent.lb[self._index],
                self.parent.ub[self._index])
    @bounds.setter
    def bounds(self, bounds_tuple):
        self.lb, self.ub = bounds_tuple

    @property
    def equality(self):
        """Returns :const:`True` when this is an equality
        constraint.

        Disable equality by assigning
        :const:`False`. Equality can only be activated by
        assigning a value to the .rhs property."""
        return self.parent.equality[self._index]
    @equality.setter
    def equality(self, equality):
        if equality:
            raise ValueError(
                "The constraint equality flag can "
                "only be set to True by assigning "
                "a value to the rhs property "
                "(e.g., con.rhs = con.lb).")
        assert not equality
        self.parent.equality[self._index] = False

    #
    # Define methods that writers expect when the
    # _linear_canonical_form flag is True
    #

    def canonical_form(self):
        from pyomo.repn.canonical_repn import \
            coopr3_CompiledLinearCanonicalRepn
        variables = []
        coefficients = []
        constant = 0
        for v, c in self.terms:
            if not v.fixed:
                variables.append(v)
                coefficients.append(c)
            else:
                constant += value(c) * v()
        repn = coopr3_CompiledLinearCanonicalRepn()
        repn.variables = tuple(variables)
        repn.linear = tuple(coefficients)
        repn.constant = constant
        return repn

class matrix_constraint(constraint_tuple):
    """
    A container for constraints of the form L <= Ax <= b.

    Args:
        A: A scipy sparse matrix or 2D numpy array (always
            copied)
        lb: A scalar or array with the same number of rows
            as A that is set to the lower bound of the
            constraints
        ub: A scalar or array with the same number of rows
            as A that is set to the upper bound of the
            constraints
        rhs: A scalar or array with the same number of rows
            as A that is set to the right-hand side the
            constraints (implies equality constraints)
        variable_order: A list with the same number of
            columns as A that stores the variable associated
            with each column
        sparse: Indicates whether or not sparse storage (CSR
            format) should be used to store A. Default is
            :const:`True`.
    """
    __slots__ = ("_A",
                 "_sparse",
                 "_lb",
                 "_ub",
                 "_equality",
                 "_variable_order")
    def __init__(self,
                 A,
                 lb=None,
                 ub=None,
                 rhs=None,
                 variable_order=None,
                 sparse=True):
        if (not has_numpy) or (not has_scipy):     #pragma:nocover
            raise ValueError("This class requires numpy and scipy")

        m, n = A.shape
        assert m > 0
        assert n > 0
        cons = (_MatrixConstraintData(i)
                for i in xrange(m))
        super(matrix_constraint, self).__init__(cons)

        if sparse:
            self._sparse = True
            self._A = scipy.sparse.csr_matrix(A,
                                              dtype=float,
                                              copy=True)
        else:
            self._sparse = False
            self._A = numpy.array(A,
                                  dtype=float,
                                  copy=True)
        self._lb = numpy.ndarray(m, dtype=float)
        self._ub = numpy.ndarray(m, dtype=float)
        self._equality = numpy.ndarray(m, dtype=bool)
        self._equality.fill(False)

        # now use the setters to fill the arrays
        self.variable_order = variable_order
        if rhs is None:
            self.lb = lb
            self.ub = ub
        else:
            if ((lb is not None) or \
                (ub is not None)):
                raise ValueError("The 'rhs' keyword can not "
                                 "be used with the 'lb' or "
                                 "'ub' keywords to initialize"
                                 " a constraint.")
            self.rhs = rhs

    @property
    def sparse(self):
        """Boolean indicating whether or not the underlying
        matrix uses sparse storage"""
        return self._sparse

    @property
    def variable_order(self):
        """The list of variables associated with the columns
        of the constraint matrix"""
        return self._variable_order
    @variable_order.setter
    def variable_order(self, variable_order):
        if variable_order is None:
            self._variable_order = None
        else:
            variable_order = tuple(variable_order)
            m,n = self._A.shape
            if len(variable_order) != n:
                raise ValueError(
                    "Argument length must be %s "
                    "not %s" % (n, len(variable_order)))
            self._variable_order = variable_order

    @property
    def lb(self):
        """The array of constraint lower bounds"""
        return self._lb.view()
    @lb.setter
    def lb(self, lb):
        if self.equality.any():
            raise ValueError(
                "The lb array can not be set "
                "when there are indices of the "
                "equality array that are True")
        if lb is None:
            lb = -numpy.inf
        if isinstance(lb, numpy.ndarray):
            numpy.copyto(self._lb, lb)
        elif isinstance(lb, NumericValue):
            raise ValueError("lb must be set to "
                             "a simple numeric type "
                             "or a numpy array")
        else:
            self._lb.fill(lb)

    @property
    def ub(self):
        """The array of constraint upper bounds"""
        return self._ub.view()
    @ub.setter
    def ub(self, ub):
        if self.equality.any():
            raise ValueError(
                "The ub array can not be set "
                "when there are indices of the "
                "equality array that are True")
        if ub is None:
            ub = numpy.inf
        if isinstance(ub, numpy.ndarray):
            numpy.copyto(self._ub, ub)
        elif isinstance(ub, NumericValue):
            raise ValueError("ub must be set to "
                             "a simple numeric type "
                             "or a numpy array")
        else:
            self._ub.fill(ub)

    @property
    def rhs(self):
        """The array of constraint right-hand sides. Can be
        set to a scalar or a numpy array of the same
        dimension. This property can only be read when the
        equality property is :const:`True` on every
        index. Assigning to this property implicitly sets
        the equality property to :const:`True` on every
        index."""
        if not self.equality.all():
            raise ValueError(
                "The rhs array can not be read when "
                "there are indices of the equality array "
                "that are False.")
        return self._lb.view()
    @rhs.setter
    def rhs(self, rhs):
        if rhs is None:
            # None has a different meaning depending on the
            # context (lb or ub), so there is no way to
            # interpret this
            raise ValueError(
                "Constraint right-hand side can not "
                "be assigned a value of None.")
        elif isinstance(rhs, NumericValue):
            raise ValueError("rhs must be set to "
                             "a simple numeric type "
                             "or a numpy array")
        elif isinstance(rhs, numpy.ndarray):
            numpy.copyto(self._lb, rhs)
            numpy.copyto(self._ub, rhs)
        else:
            self._lb.fill(rhs)
            self._ub.fill(rhs)
        self._equality.fill(True)

    @property
    def equality(self):
        """The array of boolean entries indicating the
        indices that are equality constraints"""
        return self._equality.view()
    @equality.setter
    def equality(self, equality):
        if equality:
            raise ValueError(
                "The constraint equality flag can "
                "only be set to True by assigning "
                "an expression to the rhs property "
                "(e.g., con.rhs = con.lb).")
        assert not equality
        self._equality.fill(False)

    def __call__(self, exception=True):
        """Compute the value of the body of this constraint"""
        if self.variable_order is None:
            raise ValueError(
                "No variable order has been assigned")
        values = numpy.array([v.value for v in self.variable_order],
                             dtype=float)
        if numpy.isnan(values).any():
            if exception:
                raise ValueError("One or more variables "
                                 "do not have a value")
            return None
        return self._A.dot(values)

    @property
    def lslack(self, body=_noarg):
        """Lower slack (body - lb)"""
        # this method is written so that constraint
        # types that build the body expression on the
        # fly do not have to here
        if body is _noarg:
            body = self(exception=False)
        if body is None:
            return None
        return body - self.lb

    @property
    def uslack(self, body=_noarg):
        """Upper slack (ub - body)"""
        # this method is written so that constraint
        # types that build the body expression on the
        # fly do not have to here
        if body is _noarg:
            body = self(exception=False)
        if body is None:
            return None
        return self.ub - body

    @property
    def slack(self):
        """min(lslack, uslack)"""
        # this method is written so that constraint
        # types that build the body expression on the
        # fly do not have to here
        body = self(exception=False)
        if body is None:
            return None
        lslack = self.__class__.lslack.fget(self, body=body)
        uslack = self.__class__.uslack.fget(self, body=body)
        return numpy.minimum(lslack, uslack)
