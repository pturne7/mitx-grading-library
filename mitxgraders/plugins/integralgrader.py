"""
integralgrader.py

Contains IntegralGrader, a class for grading an integral problem, consisting of lower
and upper limits, and integration variable, and an integrand.
"""

from __future__ import division
from functools import wraps
from numbers import Number
from scipy import integrate
from numpy import real, imag
from mitxgraders.sampling import (VariableSamplingSet, FunctionSamplingSet, RealInterval,
                                  DiscreteSet, gen_symbols_samples, construct_functions,
                                  construct_constants)
from mitxgraders.baseclasses import AbstractGrader, InvalidInput, ConfigError
from mitxgraders.voluptuous import Schema, Required, Any, All, Extra
from mitxgraders.helpers.calc import (CalcError, evaluator)
from mitxgraders.helpers.validatorfuncs import (Positive, NonNegative,
                                                PercentageString, is_callable)
from mitxgraders.helpers.mathfunc import within_tolerance

__all__ = ["IntegralGrader"]

def is_valid_variable_name(varname):
    """
    Tests if a variable name is valid.

    Variable names must:
        - begin with an alphabetic character
    Variable names can:
        - contain arbitrarily many single quotes as their final characters
            x or x' or x''' but not x''yz
        - contain any sequence of alphanumeric characters and underscores between
            their first character and ending single quotes

    Usage:
    >>> is_valid_variable_name('x')
    True
    >>> is_valid_variable_name('x_')
    True
    >>> is_valid_variable_name('_x')
    False
    >>> is_valid_variable_name('df_1')
    True
    >>> is_valid_variable_name("cat''")
    True
    >>> is_valid_variable_name("cat''dog")
    False
    """
    varname_pieces = varname.split("'")
    if len(varname_pieces) > 1:
        if not all(piece == "" for piece in varname_pieces[1:]):
            return False
    front = varname_pieces[0]
    return front[0].isalpha() and front.replace("_", "").isalnum()


def transform_list_to_dict(thelist, thedefaults, key_to_index_map):
    """
    Transform thelist into a dict with keys according to key_map and defaults
    from thedefaults.
    """
    return {
        key: thelist[key_to_index_map[key]]
        if key_to_index_map[key] is not None else thedefaults[key]
        for key in key_to_index_map
    }

class IntegrationError(Exception):
    """Represents an error associated with integration"""
    pass

def check_output_is_real(func, error, message):
    """Decorate a function to check that its output is real or raise error with specifed message."""

    @wraps(func)
    def wrapper(x):
        output = func(x)
        if not isinstance(output, float):
            raise error(message)
        return output

    return wrapper

class IntegralGrader(AbstractGrader):
    """
    Grades a student-entered integral by comparing numerically with
    an author-specified integral.

    WARNINGS
    =======
    This grader numerically evaluates the student- and instructor-specified
    integrals using scipy.integrate.quad. This quadrature-based integration
    technique is efficient and flexible. It handles many integrals with
    poles in the integrand and can integrate over infinite domains.

    However, some integrals may behave badly. These include, but are not limited to,
    the following:

        - integrals with highly oscillatory integrands
        - integrals that evaluate analytically to zero

    In some cases, problems might be avoided by using the integrator_options
    configuration key to provide extra instructions to scipy.integrate.quad.

    Additionally, take care that the integration limits are real-valued.
    For example, if sqrt(1-a^2) is an integration limit, the sampling range
    for variable 'a' must gaurantee that the limit sqrt(1-a^2) is real. By
    default, variables sample from the real interval [1,3].

    Configuration Options
    =====================
        answers (dict, required): Specifies author's answer. Has required keys lower,
            upper, integrand, integration_variable, which each take string values.

        complex_integrand (bool): Specifies whether the integrand is allowed to
            be complex-valued. Defaults to False.

        input_positions (dict): Specifies which integration parameters the student
            is required to enter. The default value of input_positions is:

                input_positions = {
                    'lower': 1,
                    'upper': 2,
                    'integrand': 3,
                    'integration_variable': 4
                }

            and requires students to enter all four parameters in the indicated
            order.

            If the author overrides the default input_positions value, any subset
            of the keys ('lower', 'upper', 'integrand', 'integration_variable')
            may be specified. Key values should be

                - continuous integers starting at 1, or
                - (default) None, indicating that the parameter is not entered by student

            For example,

                innput_positions = {
                    'lower': 1,
                    'upper': 2,
                    'integrand': 3
                }

            indicates that the problem has 3 input boxes which represent the
            lower limit, upper limit, and integrand in that order. The
            integration_variable is NOT entered by student and is instead the
            value specified by author in 'answers'.

        integrator_options (dict): A dictionary of keyword-arguments that are passed
            directly to scipy.integrate.quad. See
            https://docs.scipy.org/doc/scipy-0.16.1/reference/generated/scipy.integrate.quad.html
            for more information.

    Additional Configuration Options
    ================================
    The configuration keys below are the same as used by FormulaGrader and
    have the same defaults, except where specified
        user_constants: same as FormulaGrader, but with additional default 'infty'
        whitelist
        blacklist
        tolerance
        case_sensitive
        samples (default: 1)
        variables
        sample_from
        failable_evals
    """

    @property
    def schema_config(self):
        """Define the configuration options for IntegralGrader"""
        # Construct the default AbstractGrader schema
        schema = super(IntegralGrader, self).schema_config
        default_input_positions = {
            'lower': 1,
            'upper': 2,
            'integrand': 3,
            'integration_variable': 4
        }
        # Append options
        return schema.extend({
            Required('answers'): {
                Required('lower'): str,
                Required('upper'): str,
                Required('integrand'): str,
                Required('integration_variable'): str
            },
            Required('input_positions', default=default_input_positions): {
                Required('lower', default=None): Any(None, Positive(int)),
                Required('upper', default=None): Any(None, Positive(int)),
                Required('integrand', default=None): Any(None, Positive(int)),
                Required('integration_variable', default=None): Any(None, Positive(int)),
            },
            Required('integrator_options', default={'full_output': 1}): {
                Required('full_output', default=1): 1,
                Extra: object
            },
            Required('complex_integrand', default=False): bool,
            # Most of the below are copied from FormulaGrader
            Required('user_functions', default={}):
                {Extra: Any(is_callable, [is_callable], FunctionSamplingSet)},
            Required('user_constants', default={}): {Extra: Number},
            Required('blacklist', default=[]): [str],
            Required('whitelist', default=[]): [Any(str, None)],
            Required('tolerance', default='0.01%'): Any(PercentageString, NonNegative(Number)),
            Required('case_sensitive', default=True): bool,
            Required('samples', default=1): Positive(int),  # default changed to 1
            Required('variables', default=[]): [str],
            Required('sample_from', default={}): dict,
            Required('failable_evals', default=0): NonNegative(int)
        })

    debug_appendix_template = (
        "\n"
        "==============================================\n"
        "Integration Data for Sample Number {samplenum}\n"
        "==============================================\n"
        "Variables: {variables}\n"
        "\n"
        "========== Student Integration Data, Real Part\n"
        "Numerical Value: {student_re_eval}\n"
        "Error Estimate: {student_re_error}\n"
        "Number of integrand evaluations: {student_re_neval}\n"
        "========== Student Integration Data, Imaginary Part\n"
        "Numerical Value: {student_im_eval}\n"
        "Error Estimate: {student_im_error}\n"
        "Number of integrand evaluations: {student_im_neval}\n"
        "\n"
        "========== Author Integration Data, Real Part\n"
        "Numerical Value: {author_re_eval}\n"
        "Error Estimate: {author_re_error}\n"
        "Number of integrand evaluations: {author_re_neval}\n"
        "========== Author Integration Data, Imaginary Part\n"
        "Numerical Value: {author_im_eval}\n"
        "Error Estimate: {author_im_error}\n"
        "Number of integrand evaluations: {author_im_neval}\n"
        ""
    )

    @staticmethod
    def validate_input_positions(input_positions):
        used_positions_list = [input_positions[key] for key in input_positions
                               if input_positions[key] is not None]
        used_positions_set = set(used_positions_list)
        if len(used_positions_list) > len(used_positions_set):
            raise ConfigError("Key input_positions has repeated indices.")
        if used_positions_set != set(range(1, len(used_positions_set)+1)):
            msg = "Key input_positions values must be consecutive positive integers starting at 1"
            raise ConfigError(msg)

        return {
            key: input_positions[key] - 1  # Turn 1-based indexing into 0-based indexing
            if input_positions[key] is not None else None
            for key in input_positions
        }

    def __init__(self, config=None, **kwargs):
        super(IntegralGrader, self).__init__(config, **kwargs)
        self.true_input_positions = self.validate_input_positions(self.config['input_positions'])

        # The below are copied from FormulaGrader.__init__

        # Set up the various lists we use
        self.functions, self.random_funcs = construct_functions(self.config["whitelist"],
                                                                self.config["blacklist"],
                                                                self.config["user_functions"])
        self.constants = construct_constants(self.config["user_constants"])
        # TODO I would like to move this into construct_constants at some point,
        # perhaps giving construct_constants and optional argument specifying additional defaults
        if 'infty' not in self.constants:
            self.constants['infty'] = float('inf')

        # Construct the schema for sample_from
        # First, accept all VariableSamplingSets
        # Then, accept any list that RealInterval can interpret
        # Finally, single numbers or tuples of numbers will be handled by DiscreteSet
        schema_sample_from = Schema({
            Required(varname, default=RealInterval()):
                Any(VariableSamplingSet,
                    All(list, lambda pair: RealInterval(pair)),
                    lambda tup: DiscreteSet(tup))
            for varname in self.config['variables']
        })
        self.config['sample_from'] = schema_sample_from(self.config['sample_from'])
        # Note that voluptuous ensures that there are no orphaned entries in sample_from

    def validate_user_integration_variable(self, varname):
        """Check the integration variable has no other meaning and is valid variable name"""
        if (varname in self.functions or varname in self.random_funcs or varname in self.constants):
            msg = ("Cannot use {} as integration variable; it is already has "
                   "another meaning in this problem.")
            raise InvalidInput(msg.format(varname))

        if not is_valid_variable_name(varname):
            msg = ("Integration variable {} is an invalid variable name."
                   "Variable name should begin with a letter and contain alphanumeric"
                   "characters or underscores thereafter, but may end in single quotes.")
            raise InvalidInput(msg.format(varname))

    def structure_and_validate_input(self, student_input):
        used_inputs = [key for key in self.true_input_positions
                       if self.true_input_positions[key] is not None]
        if len(used_inputs) != len(student_input):
            sorted_inputs = sorted(used_inputs, key=lambda x: self.true_input_positions[x])
            msg = ("Expected {expected} student inputs but found {found}. "
                   "Inputs should  appear in order {order}.")
            raise ConfigError(msg.format(expected=len(used_inputs),
                                         found=len(student_input),
                                         order=sorted_inputs)
                              )

        structured_input = transform_list_to_dict(student_input,
                                                  self.config['answers'],
                                                  self.true_input_positions)

        return structured_input

    def check(self, answers, student_input):
        """Validates and cleans student_input, then checks response and handles errors"""
        answers = self.config['answers'] if answers is None else answers
        structured_input = self.structure_and_validate_input(student_input)
        for key in structured_input:
            if structured_input[key] == '':
                msg = "Please enter a value for {key}, it cannot be empty."
                raise InvalidInput(msg.format(key=key))
        self.validate_user_integration_variable(structured_input['integration_variable'])

        # Now perform the computations
        try:
            return self.raw_check(answers, structured_input)
        except (CalcError, InvalidInput, ConfigError):
            # These errors have been vetted already
            raise
        except IntegrationError as e:
            msg = "There appears to be an error with the integral you entered: {}"
            raise IntegrationError(msg.format(e.message))
        except Exception:  # pragma: no cover
            # If debug mode is on, give the full stack trace
            if self.config["debug"]:
                raise
            else:
                # Otherwise, give a generic error message
                msg = "Invalid Input: Could not parse your input"
                raise InvalidInput(msg)

    def raw_check(self, answer, cleaned_input):
        """Perform the numerical check of student_input vs answer"""

        var_samples = gen_symbols_samples(self.config['variables'],
                                          self.config['samples'],
                                          self.config['sample_from'])

        func_samples = gen_symbols_samples(self.random_funcs.keys(),
                                           self.config['samples'],
                                           self.random_funcs)

        # Make a copy of the functions and variables lists
        # We'll add the sampled functions/variables in
        funcscope = self.functions.copy()
        varscope = self.constants.copy()

        num_failures = 0
        for i in range(self.config['samples']):
            # Update the functions and variables listings with this sample
            funcscope.update(func_samples[i])
            varscope.update(var_samples[i])

            # Evaluate integrals. Error handling here is in two parts because
            # 1. custom error messages we've added
            # 2. scipy's warnings re-raised as error messages
            try:
                expected_re, expected_im = self.evaluate_int(
                    answer['integrand'],
                    answer['lower'],
                    answer['upper'],
                    answer['integration_variable'],
                    varscope=varscope,
                    funcscope=funcscope
                )
            except IntegrationError as e:
                msg = "Integration Error with author's stored answer: {}"
                raise ConfigError(msg.format(e.message))

            student_re, student_im = self.evaluate_int(
                cleaned_input['integrand'],
                cleaned_input['lower'],
                cleaned_input['upper'],
                cleaned_input['integration_variable'],
                varscope=varscope,
                funcscope=funcscope
                )

            # scipy raises integration warnings when things go wrong,
            # except they aren't really warnings, they're just printed to stdout
            # so we use quad's full_output option to catch the messages, and then raise errors.
            # The 4th component only exists when its warning message is non-empty
            if len(student_re) == 4:
                raise IntegrationError(student_re[3])
            if len(expected_re) == 4:
                raise ConfigError(expected_re[3])
            if len(student_im) == 4:
                raise IntegrationError(student_im[3])
            if len(expected_im) == 4:
                raise ConfigError(expected_im[3])

            self.log(self.debug_appendix_template.format(
                samplenum=i,
                variables=varscope,
                student_re_eval=student_re[0],
                student_re_error=student_re[1],
                student_re_neval=student_re[2]['neval'],
                student_im_eval=student_im[0],
                student_im_error=student_im[1],
                student_im_neval=student_im[2]['neval'],
                author_re_eval=expected_re[0],
                author_re_error=expected_re[1],
                author_re_neval=expected_re[2]['neval'],
                author_im_eval=expected_im[0],
                author_im_error=expected_im[1],
                author_im_neval=expected_im[2]['neval'],
            ))

            # Check if expressions agree
            expected = expected_re[0] + (expected_im[0] or 0)*1j
            student = student_re[0] + (student_im[0] or 0)*1j
            if not within_tolerance(expected, student, self.config['tolerance']):
                num_failures += 1
                if num_failures > self.config["failable_evals"]:
                    return {'ok': False, 'grade_decimal': 0, 'msg': ''}

        # This response appears to agree with the expected answer
        return {
            'ok': True,
            'grade_decimal': 1,
            'msg': ''
        }

    def evaluate_int(self, integrand_str, lower_str, upper_str, integration_var,
                     varscope=None, funcscope=None):
        varscope = {} if varscope is None else varscope
        funcscope = {} if funcscope is None else funcscope

        lower, _ = evaluator(lower_str,
                             case_sensitive=self.config['case_sensitive'],
                             variables=varscope,
                             functions=funcscope,
                             suffixes={})
        upper, _ = evaluator(upper_str,
                             case_sensitive=self.config['case_sensitive'],
                             variables=varscope,
                             functions=funcscope,
                             suffixes={})

        if isinstance(lower, complex) or isinstance(upper, complex):
            raise IntegrationError('Integration limits must be real but have evaluated to complex numbers.')

        # It is possible that the integration variable might appear in the limits.
        # Some consider this bad practice, but many students do it and Mathematica allows it.
        # We're going to edit the varscope below to contain the integration variable.
        # Let's store the integration variable's initial value in case it has one.
        int_var_initial = varscope[integration_var] if integration_var in varscope else None

        def raw_integrand(x):
            varscope[integration_var] = x
            value, _ = evaluator(integrand_str,
                                 case_sensitive=self.config['case_sensitive'],
                                 variables=varscope,
                                 functions=funcscope,
                                 suffixes={})
            return value

        if self.config['complex_integrand']:
            integrand_re = lambda x: real(raw_integrand(x))
            integrand_im = lambda x: imag(raw_integrand(x))
            result_re = integrate.quad(integrand_re, lower, upper, **self.config['integrator_options'])
            result_im = integrate.quad(integrand_im, lower, upper, **self.config['integrator_options'])
        else:
            errmsg = "Integrand has evaluated to complex number but must evaluate to a real."
            integrand = check_output_is_real(raw_integrand, IntegrationError, errmsg)
            result_re = integrate.quad(integrand, lower, upper, **self.config['integrator_options'])
            result_im = (None, None, {'neval':None})

        # Restore the integration variable's initial value now that we are done integrating
        if int_var_initial is not None:
            varscope[integration_var] = int_var_initial

        return result_re, result_im
