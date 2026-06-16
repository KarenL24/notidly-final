\version "2.26" 

\paper {
  #(set-paper-size "letter")
  left-margin = 15\mm
  right-margin = 15\mm
  top-margin = 15\mm
  bottom-margin = 15\mm
  line-width = 185\mm
  ragged-right = ##f
  ragged-last = ##f
  ragged-bottom = ##t
}
\include "lilypond-book-preamble.ly"
    
color = #(define-music-function (parser location color) (string?) #{
        \once \override NoteHead.color = #(x11-color color)
        \once \override Stem.color = #(x11-color color)
        \once \override Rest.color = #(x11-color color)
        \once \override Beam.color = #(x11-color color)
     #})
    
\header { 
 
  } 
 
\score  { 
 
      << \new Staff  = xacafwwwbye \with  { 
         \autoBeamOff 
          } 
          { \clef "treble" 
             \key bes \major 
             \time 3/4
             c' 4  
             f' 2  ~  
             \bar "|"  %{ end measure 1 %} 
             f' 8.  
             r 8.  
             cis' 8.  
             \set stemLeftBeamCount = #1
             ees' 8. ]  ~  
             \bar "|"  %{ end measure 2 %} 
             \set stemRightBeamCount = #1
             \once \override Stem.direction = #UP 
             ees' 8. [  
             \set stemLeftBeamCount = #2
             \once \override Stem.direction = #UP 
             fis' 16 ]  
             gis' 4  ~  
             gis' 16  
             g' 8  
             r 16  
             \bar "|"  %{ end measure 3 %} 
             r 16  
             f' 8.  
             r 8  
             d' 8  
             g' 4  ~  
             \bar "|"  %{ end measure 4 %} 
             g' 16  
             gis' 4  
             r 8  
             f' 8.  
             \set stemLeftBeamCount = #1
             cis' 8 ]  ~  
             \bar "|"  %{ end measure 5 %} 
             \set stemRightBeamCount = #1
             \once \override Stem.direction = #UP 
             cis' 8. [  
             \set stemLeftBeamCount = #2
             \once \override Stem.direction = #UP 
             f' 16 ]  
             \set stemRightBeamCount = #1
             \once \override Stem.direction = #UP 
             g' 8. [  
             \set stemLeftBeamCount = #1
             \once \override Stem.direction = #UP 
             a' 8 ]  
             g' 8  
             r 16  
             \bar "|"  %{ end measure 6 %} 
             r 16  
             f' 8.  
             g' 4  ~  
             g' 16  
             a' 8.  ~  
             \bar "|"  %{ end measure 7 %} 
             a' 4  ~  
             a' 16  
             r 8.  
             \set stemRightBeamCount = #1
             \once \override Stem.direction = #UP 
             f' 8. [  
             \set stemLeftBeamCount = #2
             \once \override Stem.direction = #UP 
             a' 16 ]  ~  
             \bar "|"  %{ end measure 8 %} 
             a' 8  
             g' 4  
             c'' 4.  
             \bar "|"  %{ end measure 9 %} 
             r 8  
             a' 4.  
             f' 4  ~  
             \bar "|"  %{ end measure 10 %} 
             f' 2.  ~  
             \bar "|"  %{ end measure 11 %} 
             f' 8  
             \bar "|."  %{ end measure 12 %} 
              } 
            
\addlyrics { \set alignBelowContext = #"xacafwwwbye"  
              "I" 
              "know" 
              "belong" 
               _  
               _  
               _  
              "to" 
              "somebody" 
               _  
              "new," 
               _  
               _  
               _  
               _  
               _  
              "tonight" 
              "you" 
               _  
              "belong" 
              "to" 
               _  
               _  
               _  
               _  
               _  
               _  
               } 
              
        >>
      
  } 
 

\layout {
  \context {
    \RemoveEmptyStaves
    \override VerticalAxisGroup.remove-first = ##t
  }
 }
 
